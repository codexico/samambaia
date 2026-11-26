"""OpenAI-compatible endpoint (/openai/v1) exposing Samambaia coordinator models inside Open WebUI."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from samambaia.config import load_config
from samambaia.coordinator import (
    BrowserMcpType,
    TaskType,
    execute_coordinated_turn,
    plan_turn,
    stream_direct_ollama,
)
from samambaia.deferred import add_deferred_task, list_deferred_tasks
from samambaia.sessions import format_history_context

router = APIRouter(prefix="/openai/v1")

VIRTUAL_MODELS: dict[str, dict[str, Any]] = {
    "samambaia-auto": {
        "name": "samambaia-auto",
        "task_type": None,
        "browser_mcp": None,
        "description": "Samambaia Coordinator (Auto-Routing + Web + Zettelkasten + Terminal + PR + Deep)",
    },
    "samambaia-terminal": {
        "name": "samambaia-terminal",
        "task_type": "terminal",
        "browser_mcp": None,
        "description": "Linux, Web Search & Zettelkasten Specialist (gemma4:26b)",
    },
    "samambaia-pr-review": {
        "name": "samambaia-pr-review",
        "task_type": "pr_review",
        "browser_mcp": None,
        "description": "Read-Only Pull Request Reviewer (gemma4:26b)",
    },
    "samambaia-coding": {
        "name": "samambaia-coding",
        "task_type": "coding",
        "browser_mcp": None,
        "description": "Software Engineering Specialist (qwen3.5:35b-a3b)",
    },
    "samambaia-deep": {
        "name": "samambaia-deep",
        "task_type": "deep",
        "browser_mcp": None,
        "description": "Deep Thinking Software Architect (qwen3.5:35b-a3b)",
    },
    "samambaia-browser-chrome": {
        "name": "samambaia-browser-chrome",
        "task_type": "terminal",
        "browser_mcp": "chrome",
        "description": "Agent with Chrome DevTools MCP attached",
    },
    "samambaia-browser-firefox": {
        "name": "samambaia-browser-firefox",
        "task_type": "terminal",
        "browser_mcp": "firefox",
        "description": "Agent with Playwright Firefox MCP attached",
    },
}

CONFIRM_MARKER = "<!-- samambaia-confirm-pending -->"


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(content or "")


def _split_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, str, str | None, str | None]:
    turns: list[dict[str, str]] = []
    pending_user = ""
    user_msgs: list[str] = []
    assistant_msgs: list[str] = []

    for m in messages:
        role = m.get("role")
        txt = _extract_text(m.get("content")).strip()
        if role == "user":
            user_msgs.append(txt)
            pending_user = txt
        elif role == "assistant":
            assistant_msgs.append(txt)
            if pending_user:
                turns.append({"user": pending_user, "assistant": txt})
                pending_user = ""

    last_user = user_msgs[-1] if user_msgs else ""
    prev_assistant = assistant_msgs[-1] if assistant_msgs else None
    prev_user = user_msgs[-2] if len(user_msgs) >= 2 else None
    history_ctx = format_history_context(turns)
    return last_user, history_ctx, prev_assistant, prev_user


def _sse_chunk(
    cid: str, model: str, text: str | None = None, finish: str | None = None
) -> str:
    delta: dict[str, Any] = {}
    if text is not None:
        delta["content"] = text
    obj = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _instant_reply(
    model: str, text: str, stream: bool
) -> StreamingResponse | JSONResponse:
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    if not stream:
        return JSONResponse(
            {
                "id": cid,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    async def _gen() -> AsyncIterator[str]:
        yield _sse_chunk(cid, model, text=text)
        yield _sse_chunk(cid, model, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.get("/models")
async def list_models() -> dict[str, Any]:
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": mid,
                "object": "model",
                "created": now,
                "owned_by": "samambaia",
                "description": info["description"],
            }
            for mid, info in VIRTUAL_MODELS.items()
        ],
    }


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body: dict[str, Any] = await request.json()
    model_id: str = body.get("model") or "samambaia-auto"
    wants_stream = bool(body.get("stream", False))
    messages: list[dict[str, Any]] = body.get("messages") or []

    cfg = load_config()
    bridge_url = f"http://{cfg.daemon_host}:{cfg.daemon_port}/v1"
    default_cwd = os.path.expanduser("~")

    last_user, history_ctx, prev_assistant, prev_user = _split_messages(messages)

    # Fast-path for Open WebUI background tasks (title, tags, follow-up generation)
    if "### Task:" in last_user or "Generate a concise" in last_user:
        router_model = (cfg.model_preferences.get("router") or ["gemma4:e4b"])[0]
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        if wants_stream:
            async def _title_stream() -> AsyncIterator[str]:
                async for ev in stream_direct_ollama(
                    last_user, router_model, cfg.ollama_host
                ):
                    yield _sse_chunk(cid, model_id, text=ev["text"])
                yield _sse_chunk(cid, model_id, finish="stop")
                yield "data: [DONE]\n\n"

            return StreamingResponse(_title_stream(), media_type="text/event-stream")

        chunks: list[str] = []
        async for ev in stream_direct_ollama(
            last_user, router_model, cfg.ollama_host
        ):
            chunks.append(ev["text"])
        return _instant_reply(model_id, "".join(chunks), stream=False)

    cmd_lower = last_user.strip().lower()
    if cmd_lower in ("!later", "/later"):
        pending = list_deferred_tasks()
        if not pending:
            return _instant_reply(
                model_id,
                "Nenhuma tarefa pendente na fila **Deixar para depois**.",
                wants_stream,
            )
        lines = ["### 📋 Fila 'Deixar para depois'\n"]
        for item in pending:
            lines.append(
                f"- **`#{item['id']}`** (`{item['task_type']} -> {item['model']}` em `{item['cwd']}`): {item['prompt'][:120]}"
            )
        lines.append(
            "\nRode `samambaia --run-later` (ou `aaaia --run-later`) no terminal para executar todas."
        )
        return _instant_reply(model_id, "\n".join(lines), wants_stream)

    vinfo = VIRTUAL_MODELS.get(model_id, VIRTUAL_MODELS["samambaia-auto"])
    forced_task: TaskType | None = vinfo["task_type"]
    browser_mcp: BrowserMcpType | None = vinfo["browser_mcp"]

    effective_cwd = default_cwd
    effective_prompt = last_user
    if effective_prompt.startswith("cwd:"):
        first_line, _, rest = effective_prompt.partition("\n")
        parts = first_line.split(maxsplit=1)
        cand_path = os.path.expanduser(parts[0][4:].strip())
        if os.path.isdir(cand_path):
            effective_cwd = os.path.abspath(cand_path)
            effective_prompt = (parts[1] if len(parts) > 1 else "") + (
                "\n" + rest if rest else ""
            )
            effective_prompt = effective_prompt.strip()

    bypass_confirm = False
    if effective_prompt.startswith(("!y ", "!sim ")):
        bypass_confirm = True
        effective_prompt = effective_prompt.split(maxsplit=1)[1]

    if prev_assistant and CONFIRM_MARKER in prev_assistant and prev_user:
        ans = cmd_lower
        if ans in ("x", "cancelar", "cancel", "n", "nao", "não"):
            return _instant_reply(
                model_id,
                "⏹️ Operação cancelada (nenhum modelo pesado foi carregado).",
                wants_stream,
            )
        if ans in ("d", "depois", "later"):
            plan_prev = await plan_turn(
                prompt=prev_user,
                cwd=effective_cwd,
                force_task_type=forced_task,
                browser_mcp=browser_mcp,
            )
            saved = add_deferred_task(
                prompt=prev_user,
                cwd=plan_prev["cwd"],
                task_type=plan_prev["task_type"],
                model=plan_prev["model"],
                reason=plan_prev.get("reason", ""),
            )
            return _instant_reply(
                model_id,
                f"✅ **Tarefa `#{saved['id']}` salva para depois!**\n"
                f"- **Perfil:** `{saved['task_type']} -> {saved['model']}`\n"
                f"- Para listar no chat envie `!later`, ou rode `aaaia --run-later` no terminal quando quiser.",
                wants_stream,
            )
        if ans in ("c", "continuar", "s", "sim", "y", "yes"):
            effective_prompt = prev_user
            bypass_confirm = True

    plan = await plan_turn(
        prompt=effective_prompt,
        cwd=effective_cwd,
        force_task_type=forced_task,
        history_context=history_ctx,
        browser_mcp=browser_mcp,
    )

    if (
        plan.get("needs_confirmation")
        and not bypass_confirm
        and model_id == "samambaia-auto"
    ):
        reasons_md = "; ".join(plan.get("warning_reasons") or [])
        warn_msg = (
            f"{CONFIRM_MARKER}\n"
            f"### ⚠️ Aviso: Tarefa longa ou troca para modelo pesado\n"
            f"- **Perfil / Modelo:** `{plan['task_type']} -> {plan['model']}`\n"
            f"- **Motivo:** {plan.get('reason')}\n"
            f"- **Alerta:** {reasons_md}\n\n"
            f"Responda nesta conversa com:\n"
            f"- **`c`** para **Continuar agora**\n"
            f"- **`d`** para **Deixar para depois** *(salvar na fila)*\n"
            f"- **`x`** para **Cancelar** *(ou apenas faça outra pergunta)*"
        )
        return _instant_reply(model_id, warn_msg, wants_stream)

    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if not wants_stream:
        out_parts: list[str] = []
        async for ev in execute_coordinated_turn(
            prompt=effective_prompt,
            cwd=plan["cwd"],
            force_task_type=plan["task_type"],
            force_model=plan["model"],
            bridge_base_url=bridge_url,
            precomputed_reason=plan.get("reason"),
            history_context=history_ctx,
            browser_mcp=browser_mcp,
        ):
            if ev.get("event") == "token":
                out_parts.append(ev.get("text", ""))
        return _instant_reply(model_id, "".join(out_parts), stream=False)

    async def _stream_agent() -> AsyncIterator[str]:
        in_thought = False
        try:
            async for ev in execute_coordinated_turn(
                prompt=effective_prompt,
                cwd=plan["cwd"],
                force_task_type=plan["task_type"],
                force_model=plan["model"],
                bridge_base_url=bridge_url,
                precomputed_reason=plan.get("reason"),
                history_context=history_ctx,
                browser_mcp=browser_mcp,
            ):
                kind = ev.get("event")
                if kind == "route":
                    badge = f"*`[{ev.get('task_type')} → {ev.get('model')}]`*\n\n"
                    yield _sse_chunk(cid, model_id, text=badge)
                elif kind == "thought":
                    if not in_thought:
                        yield _sse_chunk(cid, model_id, text="<think>\n")
                        in_thought = True
                    yield _sse_chunk(cid, model_id, text=ev.get("text", ""))
                elif kind == "tool":
                    if in_thought:
                        yield _sse_chunk(cid, model_id, text="\n</think>\n\n")
                        in_thought = False
                    args_str = json.dumps(ev.get("args", {}), ensure_ascii=False)
                    if len(args_str) > 120:
                        args_str = args_str[:117] + "..."
                    yield _sse_chunk(
                        cid,
                        model_id,
                        text=f"\n> ⚙️ `{ev.get('name')}` `{args_str}`\n\n",
                    )
                elif kind == "token":
                    if in_thought:
                        yield _sse_chunk(cid, model_id, text="\n</think>\n\n")
                        in_thought = False
                    yield _sse_chunk(cid, model_id, text=ev.get("text", ""))
                elif kind == "error":
                    if in_thought:
                        yield _sse_chunk(cid, model_id, text="\n</think>\n\n")
                        in_thought = False
                    yield _sse_chunk(
                        cid, model_id, text=f"\n\n❌ **Erro:** `{ev.get('message')}`"
                    )
            if in_thought:
                yield _sse_chunk(cid, model_id, text="\n</think>\n\n")
        except Exception as exc:
            yield _sse_chunk(
                cid, model_id, text=f"\n\n❌ **Erro:** `{type(exc).__name__}: {exc}`"
            )
        yield _sse_chunk(cid, model_id, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream_agent(), media_type="text/event-stream")
