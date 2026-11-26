"""Comprehensive, deeply customizable configuration system with sensible defaults."""

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
    root = (
        pathlib.Path(base).expanduser()
        if base
        else (pathlib.Path.home() / ".config")
    )
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
USER_CONFIG_PATH = get_config_dir() / "config.json"


class InferenceSettings(pydantic.BaseModel):
    unload_other_models_on_switch: bool = True
    router_keep_alive: str = "30m"
    router_num_ctx: int = 4096
    direct_num_ctx: int = 16384
    direct_temperature: float = 0.2
    compaction_tokens: int = 24576
    ollama_timeout_seconds: float = 300.0


class GuardSettings(pydantic.BaseModel):
    always_confirm_tiers: list[str] = pydantic.Field(
        default_factory=lambda: ["deep"]
    )
    heavy_model_patterns: list[str] = pydantic.Field(
        default_factory=lambda: ["35b", "32b", "31b", "70b"]
    )
    dangerous_command_patterns: list[str] = pydantic.Field(
        default_factory=lambda: [
            "rm -rf /",
            "rm -fr /",
            "mkfs",
            "dd if=",
            ":(){",
            "chmod -R 777 /",
            "> /dev/sd",
            "> /dev/nvme",
        ]
    )


class LimitSettings(pydantic.BaseModel):
    max_session_turns: int = 20
    max_history_chars: int = 12000
    max_diff_chars: int = 24000
    max_pr_diff_chars: int = 26000
    max_webpage_chars: int = 22000
    web_search_default_results: int = 5


class FeatureSettings(pydantic.BaseModel):
    enable_web_tools: bool = True
    enable_github_pr_tools: bool = True
    chrome_mcp_args: list[str] = pydantic.Field(
        default_factory=lambda: ["-y", "chrome-devtools-mcp@latest"]
    )
    firefox_mcp_args: list[str] = pydantic.Field(
        default_factory=lambda: [
            "-y",
            "@playwright/mcp@latest",
            "--browser",
            "firefox",
        ]
    )


class ZettelkastenSettings(pydantic.BaseModel):
    enabled: bool = False
    directory: str = "~/zk"
    todo_file: str = "TODO.md"
    openwebui_collection_name: str = "zk"
    sync_interval: str = "10min"
    max_note_chars: int = 22000


class AssistantConfig(pydantic.BaseModel):
    ollama_host: str = "http://localhost:11434"
    openwebui_url: str = "http://127.0.0.1:8080"
    public_webui_url: str = "http://samambaia.localhost:8080"
    daemon_host: str = "127.0.0.1"
    daemon_port: int = 8765
    default_language: str = "Português do Brasil"
    ollama_container_name: str = "ollama"
    openwebui_container_name: str = "open-webui"
    kde_selection_shortcut: str = "Meta+Alt+T"
    model_preferences: dict[str, list[str]] = pydantic.Field(
        default_factory=lambda: {
            "router": ["gemma4:e4b", "gemma4:26b"],
            "direct": ["gemma4:e4b", "gemma4:26b"],
            "terminal": ["gemma4:26b", "gemma4:e4b"],
            "pr_review": ["gemma4:26b", "qwen3.5:35b-a3b", "gemma4:e4b"],
            "coding": ["qwen3.5:35b-a3b", "gemma4:26b", "gemma4:e4b"],
            "deep": ["qwen3.5:35b-a3b", "gemma4:26b"],
        }
    )
    inference: InferenceSettings = pydantic.Field(default_factory=InferenceSettings)
    guard: GuardSettings = pydantic.Field(default_factory=GuardSettings)
    limits: LimitSettings = pydantic.Field(default_factory=LimitSettings)
    features: FeatureSettings = pydantic.Field(default_factory=FeatureSettings)
    zettelkasten: ZettelkastenSettings = pydantic.Field(
        default_factory=ZettelkastenSettings
    )

    @property
    def openai_base_url(self) -> str:
        return f"{self.ollama_host.rstrip('/')}/v1"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    env_map = {
        "SAMAMBAIA_OLLAMA_HOST": ("ollama_host", str),
        "SAMAMBAIA_OPENWEBUI_URL": ("openwebui_url", str),
        "SAMAMBAIA_PUBLIC_WEBUI_URL": ("public_webui_url", str),
        "SAMAMBAIA_DAEMON_HOST": ("daemon_host", str),
        "SAMAMBAIA_DAEMON_PORT": ("daemon_port", int),
        "SAMAMBAIA_LANGUAGE": ("default_language", str),
        "SAMAMBAIA_SHORTCUT": ("kde_selection_shortcut", str),
    }
    for env_key, (field_name, caster) in env_map.items():
        raw = os.environ.get(env_key)
        if raw:
            data[field_name] = caster(raw)

    zk_env = os.environ.get("SAMAMBAIA_ENABLE_ZK")
    if zk_env is not None:
        data.setdefault("zettelkasten", {})["enabled"] = zk_env.lower() in (
            "1",
            "true",
            "yes",
        )
    if os.environ.get("ZK_NOTEBOOK_DIR"):
        data.setdefault("zettelkasten", {})["directory"] = os.environ["ZK_NOTEBOOK_DIR"]
    if os.environ.get("ZK_TODO_FILE"):
        data.setdefault("zettelkasten", {})["todo_file"] = os.environ["ZK_TODO_FILE"]
    return data


def load_config() -> AssistantConfig:
    """Loads config: defaults < config.example.json < config.local.json < ~/.config/samambaia/config.json < ENV."""
    candidates = [
        PROJECT_ROOT / "config.example.json",
        PROJECT_ROOT / "config.local.json",
        USER_CONFIG_PATH,
    ]
    merged: dict[str, Any] = {}
    for path in candidates:
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    merged = _deep_merge(merged, raw)
            except Exception:
                pass
    merged = _apply_env_overrides(merged)
    return AssistantConfig(**merged)


def init_user_config(*, force: bool = False) -> pathlib.Path:
    """Writes a full editable configuration file to ~/.config/samambaia/config.json."""
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if USER_CONFIG_PATH.exists() and not force:
        return USER_CONFIG_PATH
    cfg = load_config()
    USER_CONFIG_PATH.write_text(
        json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return USER_CONFIG_PATH


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
    prefs = cfg.model_preferences.get(tier) or cfg.model_preferences.get("router", [])
    for candidate in prefs:
        if candidate in installed_set:
            return candidate
    return installed[0]


async def resolve_all_tiers(cfg: AssistantConfig) -> tuple[list[str], dict[str, str]]:
    installed = await fetch_installed_models(cfg.ollama_host)
    mapping = {
        tier: pick_best_model(tier, installed, cfg)
        for tier in ("router", "direct", "terminal", "pr_review", "coding", "deep")
    }
    return installed, mapping
