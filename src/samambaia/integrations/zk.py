"""Optional Markdown Zettelkasten integration (enabled via `zettelkasten.enabled = true`)."""

from __future__ import annotations

import datetime as dt
import os
import pathlib
from typing import Any, Callable

from samambaia.config import AssistantConfig, load_config


def get_zk_dir(cfg: AssistantConfig | None = None) -> pathlib.Path:
    active_cfg = cfg or load_config()
    env_dir = os.environ.get("ZK_NOTEBOOK_DIR") or active_cfg.zettelkasten.directory
    return pathlib.Path(env_dir).expanduser().resolve()


def make_zk_tools(cfg: AssistantConfig | None = None) -> list[Callable[..., Any]]:
    active_cfg = cfg or load_config()
    default_todo = os.environ.get("ZK_TODO_FILE") or active_cfg.zettelkasten.todo_file
    max_chars = active_cfg.zettelkasten.max_note_chars

    def search_zk(query: str, max_results: int = 8) -> str:
        """Searches Markdown notes inside the Zettelkasten directory by title or content.

        Args:
            query: Keyword or phrase to search.
            max_results: Maximum number of matching notes (1 to 20, default 8).
        """
        zk_root = get_zk_dir(active_cfg)
        if not zk_root.is_dir():
            return f"Zettelkasten directory not found at {zk_root}."

        terms = [t.lower() for t in query.strip().split() if t.strip()]
        if not terms:
            return "Please provide at least one search term."

        scored: list[tuple[int, pathlib.Path, str]] = []
        for md_file in zk_root.rglob("*.md"):
            rel_parts = md_file.relative_to(zk_root).parts
            if any(p.startswith(".") for p in rel_parts):
                continue
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            name_lower = md_file.name.lower()
            content_lower = content.lower()
            score = 0
            snippet = ""
            for term in terms:
                if term in name_lower:
                    score += 5
                count = content_lower.count(term)
                if count:
                    score += min(count, 5)
                    if not snippet:
                        idx = content_lower.find(term)
                        start = max(0, idx - 80)
                        end = min(len(content), idx + 160)
                        snippet = content[start:end].replace("\n", " ").strip()
            if score > 0:
                if not snippet:
                    snippet = content[:180].replace("\n", " ").strip()
                scored.append((score, md_file, snippet))

        scored.sort(key=lambda x: (-x[0], x[1].name.lower()))
        top = scored[: max(1, min(int(max_results), 20))]
        if not top:
            return f"No notes found in {zk_root} matching '{query}'."

        lines = [f"Notes found in {zk_root}:"]
        for score, path, snip in top:
            lines.append(f"- {path.relative_to(zk_root)} (score={score}): {snip}")
        return "\n".join(lines)

    def read_zk_note(note_path: str) -> str:
        """Reads the full content of a Markdown note inside the Zettelkasten directory.

        Args:
            note_path: Relative path inside Zettelkasten or partial note filename.
        """
        zk_root = get_zk_dir(active_cfg)
        candidate = (zk_root / note_path).resolve()
        if not str(candidate).startswith(str(zk_root)):
            return "Access denied: path outside Zettelkasten root."

        if not candidate.is_file():
            needle = note_path.lower()
            matches = [
                p
                for p in zk_root.rglob("*.md")
                if needle in p.name.lower()
                and not any(part.startswith(".") for part in p.relative_to(zk_root).parts)
            ]
            if not matches:
                return f"Note '{note_path}' not found in {zk_root}."
            candidate = matches[0]

        text = candidate.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n[... note truncated at {max_chars} chars ...]"
        return f"# File: {candidate.relative_to(zk_root)}\n\n{text}"

    def append_zk_todo(task_text: str, target_note: str = "") -> str:
        """Appends a '- [ ] ...' task item to the Zettelkasten TODO Markdown file.

        Args:
            task_text: Description of the task or note to append.
            target_note: Optional target Markdown filename inside Zettelkasten.
        """
        zk_root = get_zk_dir(active_cfg)
        if not zk_root.is_dir():
            return f"Directory {zk_root} does not exist."

        chosen_note = target_note.strip() or default_todo
        target = (zk_root / chosen_note).resolve()
        if not str(target).startswith(str(zk_root)):
            return "Invalid path outside Zettelkasten root."

        stamp = dt.date.today().isoformat()
        clean_item = task_text.strip().lstrip("-").lstrip("[ ]").strip()
        line = f"- [ ] {clean_item} _({stamp})_\n"

        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        target.write_text(existing + sep + line, encoding="utf-8")
        return f"Added item to {target.relative_to(zk_root)}: {line.strip()}"

    return [search_zk, read_zk_note, append_zk_todo]
