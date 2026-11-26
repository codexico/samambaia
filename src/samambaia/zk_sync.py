"""Incremental SHA-256 synchronizer mirroring ~/zk notes into Open WebUI's '#zk' Knowledge base."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

from samambaia.config import STATE_DIR, load_config
from samambaia.zk_tools import get_zk_dir

SYNC_STATE_FILE = STATE_DIR / "zk_openwebui_sync.json"


def _load_sync_state() -> dict[str, Any]:
    if SYNC_STATE_FILE.exists():
        try:
            return json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"kb_id": None, "files": {}, "ignored_duplicates": {}}


def _save_sync_state(state: dict[str, Any]) -> None:
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _get_openwebui_token(client: httpx.Client, base_url: str) -> str:
    resp = client.post(
        f"{base_url}/api/v1/auths/signin",
        json={"email": "admin@localhost", "password": "password"},
    )
    if resp.status_code != 200:
        resp = client.post(
            f"{base_url}/api/v1/auths/signup",
            json={
                "name": "Local User",
                "email": "admin@localhost",
                "password": "password",
            },
        )
    resp.raise_for_status()
    return resp.json()["token"]


def _get_or_create_zk_kb(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    cached_id: str | None,
) -> str:
    resp = client.get(f"{base_url}/api/v1/knowledge/", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", data) if isinstance(data, dict) else data
    for kb in items:
        if kb.get("id") == cached_id or (kb.get("name") or "").lower() == "zk":
            return kb["id"]

    create_resp = client.post(
        f"{base_url}/api/v1/knowledge/create",
        headers=headers,
        json={
            "name": "zk",
            "description": "Zettelkasten notes (~/zk) automatically synced by Samambaia",
        },
    )
    create_resp.raise_for_status()
    return create_resp.json()["id"]


def sync_zk_to_openwebui() -> int:
    cfg = load_config()
    base_url = cfg.openwebui_url.rstrip("/")
    zk_root = get_zk_dir()
    if not zk_root.is_dir():
        print(f"Diretório {zk_root} não encontrado.")
        return 1

    state = _load_sync_state()
    tracked: dict[str, dict[str, str]] = state.setdefault("files", {})
    ignored_dups: dict[str, str] = state.setdefault("ignored_duplicates", {})

    current_hashes: dict[str, tuple[str, bytes]] = {}
    for md_path in sorted(zk_root.rglob("*.md")):
        if any(part.startswith(".") for part in md_path.relative_to(zk_root).parts):
            continue
        raw_bytes = md_path.read_bytes()
        if not raw_bytes.strip():
            continue
        rel_key = str(md_path.relative_to(zk_root))
        sha = hashlib.sha256(raw_bytes).hexdigest()
        current_hashes[rel_key] = (sha, raw_bytes)

    to_upsert = [
        rel_key
        for rel_key, (sha, _) in current_hashes.items()
        if tracked.get(rel_key, {}).get("sha256") != sha
        and ignored_dups.get(rel_key) != sha
    ]
    to_delete = [rel_key for rel_key in list(tracked.keys()) if rel_key not in current_hashes]

    if not to_upsert and not to_delete and state.get("kb_id"):
        print(f"✔ Nenhuma alteração em {zk_root} ({len(tracked)} notas já sincronizadas).")
        return 0

    added = 0
    updated = 0
    removed = 0

    with httpx.Client(timeout=120.0) as client:
        token = _get_openwebui_token(client, base_url)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = _get_or_create_zk_kb(client, base_url, headers, state.get("kb_id"))
        state["kb_id"] = kb_id

        for rel_key in to_delete:
            old_info = tracked.pop(rel_key, None)
            if old_info and old_info.get("file_id"):
                fid = old_info["file_id"]
                client.post(
                    f"{base_url}/api/v1/knowledge/{kb_id}/file/remove",
                    headers=headers,
                    json={"file_id": fid},
                )
                client.delete(f"{base_url}/api/v1/files/{fid}", headers=headers)
                removed += 1
            _save_sync_state(state)

        for rel_key in to_upsert:
            sha, raw_bytes = current_hashes[rel_key]
            prev = tracked.get(rel_key)
            if prev and prev.get("file_id"):
                old_fid = prev["file_id"]
                client.post(
                    f"{base_url}/api/v1/knowledge/{kb_id}/file/remove",
                    headers=headers,
                    json={"file_id": old_fid},
                )
                client.delete(f"{base_url}/api/v1/files/{old_fid}", headers=headers)

            upload_name = rel_key.replace("/", "__")
            up_resp = client.post(
                f"{base_url}/api/v1/files/",
                params={"process": "true", "process_in_background": "false"},
                headers=headers,
                files={"file": (upload_name, raw_bytes, "text/markdown")},
            )
            if up_resp.status_code >= 400:
                continue

            file_id = up_resp.json()["id"]
            add_resp = client.post(
                f"{base_url}/api/v1/knowledge/{kb_id}/file/add",
                headers=headers,
                json={"file_id": file_id},
            )
            if add_resp.status_code >= 400:
                if "Duplicate content" in add_resp.text:
                    ignored_dups[rel_key] = sha
                    _save_sync_state(state)
                continue

            ignored_dups.pop(rel_key, None)
            if prev:
                updated += 1
            else:
                added += 1
            tracked[rel_key] = {"sha256": sha, "file_id": file_id}
            _save_sync_state(state)

    _save_sync_state(state)
    print(
        f"✔ Sincronização '#zk' concluída! "
        f"(Novas: {added} | Atualizadas: {updated} | Removidas: {removed} | Total ativas: {len(tracked)})"
    )
    return 0
