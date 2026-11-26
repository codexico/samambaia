"""Multi-turn conversation persistence per working directory for CLI (-c / --chat)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import uuid
from typing import Any

from samambaia.config import STATE_DIR

SESSIONS_DIR = STATE_DIR / "chat_sessions"


def _cwd_index_path(cwd: str) -> pathlib.Path:
    digest = hashlib.sha1(cwd.encode("utf-8")).hexdigest()[:12]
    return SESSIONS_DIR / f"last_{digest}.txt"


def _session_path(session_id: str) -> pathlib.Path:
    safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
    return SESSIONS_DIR / f"{safe_id}.json"


def create_session(cwd: str) -> str:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    data = {
        "session_id": session_id,
        "cwd": cwd,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "turns": [],
    }
    _session_path(session_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _cwd_index_path(cwd).write_text(session_id, encoding="utf-8")
    return session_id


def get_or_create_last_session(cwd: str) -> str:
    idx = _cwd_index_path(cwd)
    if idx.exists():
        sid = idx.read_text(encoding="utf-8").strip()
        if sid and _session_path(sid).exists():
            return sid
    return create_session(cwd)


def load_turns(session_id: str) -> list[dict[str, str]]:
    path = _session_path(session_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("turns", [])
    except Exception:
        return []


def append_turn(
    session_id: str,
    cwd: str,
    user_text: str,
    assistant_text: str,
    *,
    task_type: str = "",
    model: str = "",
) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _session_path(session_id)
    if path.exists():
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {"session_id": session_id, "cwd": cwd, "turns": []}
    else:
        data = {"session_id": session_id, "cwd": cwd, "turns": []}

    turns = data.setdefault("turns", [])
    turns.append(
        {
            "timestamp": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "user": user_text,
            "assistant": assistant_text,
            "task_type": task_type,
            "model": model,
        }
    )
    if len(turns) > 20:
        data["turns"] = turns[-20:]

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _cwd_index_path(cwd).write_text(session_id, encoding="utf-8")


def format_history_context(
    turns: list[dict[str, str]], *, max_chars: int = 12000
) -> str:
    if not turns:
        return ""
    chunks: list[str] = []
    total = 0
    for t in reversed(turns[-10:]):
        u = (t.get("user") or "").strip()
        a = (t.get("assistant") or "").strip()
        if len(a) > 2500:
            a = a[:2500] + " [...]"
        entry = f"Usuário: {u}\nAssistente: {a}"
        if total + len(entry) > max_chars and chunks:
            break
        chunks.append(entry)
        total += len(entry)
    chunks.reverse()
    return "\n\n---\n".join(chunks)
