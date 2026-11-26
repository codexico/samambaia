"""Persistent queue for heavy tasks saved for later ('Deixar para depois')."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

from samambaia.config import STATE_DIR

DEFERRED_FILE = STATE_DIR / "deferred_tasks.json"
RESULTS_DIR = STATE_DIR / "deferred_results"


def _load_raw() -> list[dict[str, Any]]:
    if not DEFERRED_FILE.exists():
        return []
    try:
        return json.loads(DEFERRED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_raw(items: list[dict[str, Any]]) -> None:
    DEFERRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEFERRED_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_deferred_task(
    *,
    prompt: str,
    cwd: str,
    task_type: str,
    model: str,
    reason: str,
) -> dict[str, Any]:
    items = _load_raw()
    next_id = max((int(item.get("id", 0)) for item in items), default=0) + 1
    entry = {
        "id": next_id,
        "status": "pending",
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "prompt": prompt,
        "cwd": cwd,
        "task_type": task_type,
        "model": model,
        "reason": reason,
        "result_path": None,
    }
    items.append(entry)
    _save_raw(items)
    return entry


def list_deferred_tasks(*, include_done: bool = False) -> list[dict[str, Any]]:
    items = _load_raw()
    if include_done:
        return items
    return [i for i in items if i.get("status") == "pending"]


def get_deferred_task(task_id: int) -> dict[str, Any] | None:
    for item in _load_raw():
        if int(item.get("id", -1)) == task_id:
            return item
    return None


def complete_deferred_task(task_id: int, output_text: str) -> pathlib.Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = RESULTS_DIR / f"task-{task_id}.md"
    result_file.write_text(output_text, encoding="utf-8")

    items = _load_raw()
    for item in items:
        if int(item.get("id", -1)) == task_id:
            item["status"] = "done"
            item["completed_at"] = (
                dt.datetime.now().astimezone().isoformat(timespec="seconds")
            )
            item["result_path"] = str(result_file)
            break
    _save_raw(items)
    return result_file


def drop_deferred_task(task_id: int) -> bool:
    items = _load_raw()
    filtered = [i for i in items if int(i.get("id", -1)) != task_id]
    if len(filtered) == len(items):
        return False
    _save_raw(filtered)
    return True
