"""Local HTTP daemon (FastAPI) serving CLI (`samambaia` / `aaaia`) and Open WebUI (`/openai/v1`)."""

from __future__ import annotations

import json
from typing import Any

import pydantic
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, StreamingResponse

from samambaia import __version__
from samambaia.config import fetch_loaded_models, load_config, resolve_all_tiers
from samambaia.coordinator import (
    BrowserMcpType,
    TaskType,
    execute_coordinated_turn,
    plan_turn,
)
from samambaia.openai_bridge import proxy_chat_completions
from samambaia.openwebui_api import router as openwebui_router

app = FastAPI(title="Samambaia Coordinator Daemon", version=__version__)
app.include_router(openwebui_router)


class AskRequest(pydantic.BaseModel):
    prompt: str
    cwd: str | None = None
    task_type: TaskType | None = None
    model: str | None = None
    reason: str | None = None
    history_context: str | None = None
    browser_mcp: BrowserMcpType | None = None


@app.get("/")
async def root_redirect() -> RedirectResponse:
    cfg = load_config()
    return RedirectResponse(url=cfg.public_webui_url)


@app.post("/v1/chat/completions")
async def openai_bridge_endpoint(request: Request):
    cfg = load_config()
    return await proxy_chat_completions(request, cfg.ollama_host)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
async def status() -> dict[str, Any]:
    cfg = load_config()
    try:
        installed, tier_mapping = await resolve_all_tiers(cfg)
        loaded = await fetch_loaded_models(cfg.ollama_host)
        return {
            "daemon": "online",
            "web_ui": cfg.public_webui_url,
            "ollama_host": cfg.ollama_host,
            "ollama_online": True,
            "installed_models": installed,
            "loaded_models": [
                {
                    "name": m.get("name"),
                    "size_vram": m.get("size_vram", 0),
                    "size": m.get("size", 0),
                }
                for m in loaded
            ],
            "active_tiers": tier_mapping,
        }
    except Exception as exc:
        return {
            "daemon": "online",
            "ollama_host": cfg.ollama_host,
            "ollama_online": False,
            "error": str(exc),
        }


@app.post("/plan")
async def plan(req: AskRequest) -> dict[str, Any]:
    return await plan_turn(
        prompt=req.prompt,
        cwd=req.cwd,
        force_task_type=req.task_type,
        force_model=req.model,
        history_context=req.history_context,
        browser_mcp=req.browser_mcp,
    )


@app.post("/ask")
async def ask(req: AskRequest) -> StreamingResponse:
    cfg = load_config()
    bridge_url = f"http://{cfg.daemon_host}:{cfg.daemon_port}/v1"

    async def event_generator():
        try:
            async for event in execute_coordinated_turn(
                prompt=req.prompt,
                cwd=req.cwd,
                force_task_type=req.task_type,
                force_model=req.model,
                bridge_base_url=bridge_url,
                precomputed_reason=req.reason,
                history_context=req.history_context,
                browser_mcp=req.browser_mcp,
            ):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:
            yield json.dumps(
                {"event": "error", "message": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


def main() -> None:
    cfg = load_config()
    uvicorn.run(
        "samambaia.daemon:app",
        host=cfg.daemon_host,
        port=cfg.daemon_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
