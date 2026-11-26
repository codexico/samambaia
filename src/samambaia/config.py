"""Configuration loader and live Ollama model resolution (XDG-compliant, zero hardcoded paths)."""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import httpx
import pydantic

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def get_config_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = pathlib.Path(base).expanduser() if base else (pathlib.Path.home() / ".config")
    return root / "samambaia"


def get_state_dir() -> pathlib.Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = (
        pathlib.Path(base).expanduser()
        if base
        else (pathlib.Path.home() / ".local" / "state")
    )
    return root / "samambaia"


STATE_DIR = get_state_dir()
SAVE_DIR = STATE_DIR / "conversations"
APP_DATA_DIR = STATE_DIR / "appdata"


class AssistantConfig(pydantic.BaseModel):
    ollama_host: str = "http://localhost:11434"
    openwebui_url: str = "http://127.0.0.1:8080"
    public_webui_url: str = "http://samambaia.localhost:8080"
    daemon_host: str = "127.0.0.1"
    daemon_port: int = 8765
    compaction_tokens: int = 24576
    zk_todo_file: str = "TODO.md"
    model_preferences: dict[str, list[str]] = pydantic.Field(
        default_factory=lambda: {
            "router": ["gemma4:e4b", "gemma4:26b"],
            "terminal": ["gemma4:26b", "gemma4:e4b"],
            "pr_review": ["gemma4:26b", "qwen3.5:35b-a3b", "gemma4:e4b"],
            "coding": ["qwen3.5:35b-a3b", "gemma4:26b", "gemma4:e4b"],
            "deep": ["qwen3.5:35b-a3b", "gemma4:26b"],
        }
    )

    @property
    def openai_base_url(self) -> str:
        return f"{self.ollama_host.rstrip('/')}/v1"


def load_config() -> AssistantConfig:
    """Loads configuration with priority: ~/.config/samambaia/config.json > repo config.local.json > config.example.json."""
    candidates = [
        get_config_dir() / "config.json",
        PROJECT_ROOT / "config.local.json",
        PROJECT_ROOT / "config.example.json",
    ]
    merged: dict[str, Any] = {}
    # Load base defaults first, then overlay user config
    for path in reversed(candidates):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    merged.update(data)
            except Exception:
                pass
    return AssistantConfig(**merged)


async def fetch_installed_models(ollama_host: str) -> list[str]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{ollama_host.rstrip('/')}/api/tags")
        resp.raise_for_status()
        payload = resp.json()
        return [m["name"] for m in payload.get("models", [])]


async def fetch_loaded_models(ollama_host: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{ollama_host.rstrip('/')}/api/ps")
        resp.raise_for_status()
        return resp.json().get("models", [])


async def unload_model(ollama_host: str, model_name: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{ollama_host.rstrip('/')}/api/generate",
                json={"model": model_name, "keep_alive": 0},
            )
    except Exception:
        pass


def pick_best_model(tier: str, installed: list[str], cfg: AssistantConfig) -> str:
    if not installed:
        raise RuntimeError(
            "Nenhum modelo encontrado no Ollama. Rode 'ollama pull <modelo>' primeiro."
        )
    installed_set = set(installed)
    for candidate in cfg.model_preferences.get(tier, []):
        if candidate in installed_set:
            return candidate
    return installed[0]


async def resolve_all_tiers(cfg: AssistantConfig) -> tuple[list[str], dict[str, str]]:
    installed = await fetch_installed_models(cfg.ollama_host)
    mapping = {
        tier: pick_best_model(tier, installed, cfg)
        for tier in ("router", "terminal", "pr_review", "coding", "deep")
    }
    return installed, mapping
