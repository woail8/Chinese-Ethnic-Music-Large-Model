from __future__ import annotations

import json
import re
import time
from pathlib import Path


_SAFE_ID_RE = re.compile(r"^[a-fA-F0-9-]{8,64}$")


def _session_path(dir_path: Path, session_id: str) -> Path:
    return dir_path / f"{session_id}.json"


def load_session(dir_path: Path, session_id: str) -> dict:
    if not session_id or not _SAFE_ID_RE.match(session_id):
        return {"session_id": "", "summary": "", "messages": []}
    path = _session_path(dir_path, session_id)
    if not path.exists():
        return {"session_id": session_id, "summary": "", "messages": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"session_id": session_id, "summary": "", "messages": []}
    summary = str(data.get("summary", "") or "")
    messages = data.get("messages", []) or []
    if not isinstance(messages, list):
        messages = []
    cleaned = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        cleaned.append({"role": role, "content": content})
    last_prompt = data.get("last_prompt")
    if not isinstance(last_prompt, list):
        last_prompt = []
    last_ref_mode = data.get("last_ref_mode")
    if not isinstance(last_ref_mode, str):
        last_ref_mode = ""
    last_usage = data.get("last_usage")
    if not isinstance(last_usage, dict):
        last_usage = {}
    last_summary_usage = data.get("last_summary_usage")
    if not isinstance(last_summary_usage, dict):
        last_summary_usage = {}
    return {
        "session_id": session_id,
        "summary": summary,
        "messages": cleaned,
        "last_prompt": last_prompt,
        "last_ref_mode": last_ref_mode,
        "last_usage": last_usage,
        "last_summary_usage": last_summary_usage,
    }


def save_session(
    dir_path: Path,
    session_id: str,
    summary: str,
    messages: list[dict],
    *,
    last_prompt: list[dict] | None = None,
    last_ref_mode: str | None = None,
    last_usage: dict | None = None,
    last_summary_usage: dict | None = None,
) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary or "",
        "messages": messages,
        "updated_at": int(time.time()),
    }
    if last_prompt is not None:
        payload["last_prompt"] = last_prompt
    if last_ref_mode is not None:
        payload["last_ref_mode"] = last_ref_mode
    if last_usage is not None:
        payload["last_usage"] = last_usage
    if last_summary_usage is not None:
        payload["last_summary_usage"] = last_summary_usage
    _session_path(dir_path, session_id).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

