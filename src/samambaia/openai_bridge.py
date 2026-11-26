"""OpenAI-compatible bridge normalizing tool_calls and reasoning deltas for local Ollama models."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import socket
import time
import uuid
from typing import Any, AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_fallback_tool_call(
    content: str, valid_tool_names: set[str]
) -> tuple[str, dict[str, Any]] | None:
    """Detects when legacy code models emit tool calls as raw JSON fenced blocks."""
    candidates: list[str] = _JSON_FENCE_RE.findall(content)
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    for raw in candidates:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("name")
        args = obj.get("arguments", obj.get("parameters"))
        if isinstance(name, str) and name in valid_tool_names and isinstance(args, dict):
            return name, args
    return None


def _sse_from_completion(payload: dict[str, Any]) -> str:
    choice = (payload.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    finish_reason = choice.get("finish_reason") or "stop"
    created = payload.get("created") or int(time.time())
    model = payload.get("model") or "local"
    cid = payload.get("id") or f"chatcmpl-{uuid.uuid4().hex[:12]}"

    chunks: list[str] = []
    if msg.get("tool_calls"):
        tc_deltas = []
        for idx, tc in enumerate(msg["tool_calls"]):
            tc_deltas.append(
                {
                    "index": idx,
                    "id": tc.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                    "type": "function",
                    "function": tc.get("function", {}),
                }
            )
        first = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "tool_calls": tc_deltas},
                    "finish_reason": None,
                }
            ],
        }
        chunks.append(f"data: {json.dumps(first, ensure_ascii=False)}\n\n")
        finish_reason = "tool_calls"
    else:
        reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
        if reasoning:
            r_chunk = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "reasoning": reasoning,
                            "reasoning_content": reasoning,
                        },
                        "finish_reason": None,
                    }
                ],
            }
            chunks.append(f"data: {json.dumps(r_chunk, ensure_ascii=False)}\n\n")
        text = msg.get("content") or ""
        if text:
            first = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": text},
                        "finish_reason": None,
                    }
                ],
            }
            chunks.append(f"data: {json.dumps(first, ensure_ascii=False)}\n\n")

    final = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }
    chunks.append(f"data: {json.dumps(final, ensure_ascii=False)}\n\n")
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks)


async def proxy_chat_completions(
    request: Request, ollama_host: str
) -> StreamingResponse | JSONResponse:
    body: dict[str, Any] = await request.json()
    wants_stream = bool(body.get("stream", False))
    tools = body.get("tools") or []
    valid_tool_names = {
        t.get("function", {}).get("name")
        for t in tools
        if isinstance(t, dict) and t.get("function", {}).get("name")
    }

    upstream_url = f"{ollama_host.rstrip('/')}/v1/chat/completions"
    upstream_body = dict(body)
    upstream_body["stream"] = False

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(upstream_url, json=upstream_body)
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()

    choices = payload.get("choices") or []
    if choices and valid_tool_names:
        msg = choices[0].get("message") or {}
        if not msg.get("tool_calls") and msg.get("content"):
            extracted = _extract_fallback_tool_call(msg["content"], valid_tool_names)
            if extracted is not None:
                tool_name, tool_args = extracted
                msg["content"] = None
                msg["tool_calls"] = [
                    {
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args, ensure_ascii=False),
                        },
                    }
                ]
                choices[0]["finish_reason"] = "tool_calls"

    if wants_stream:
        sse_text = _sse_from_completion(payload)
        return StreamingResponse(
            iter([sse_text]), media_type="text/event-stream"
        )
    return JSONResponse(payload)


@contextlib.asynccontextmanager
async def temporary_bridge_server(ollama_host: str) -> AsyncIterator[str]:
    bridge_app = FastAPI()

    @bridge_app.post("/v1/chat/completions")
    async def _completions(req: Request):
        return await proxy_chat_completions(req, ollama_host)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        bridge_app, host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        await task
