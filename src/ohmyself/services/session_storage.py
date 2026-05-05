from __future__ import annotations

import json
import time
from hashlib import sha1
from pathlib import Path
from typing import Any

from ohmyself.api.usage import UsageSnapshot
from ohmyself.config.paths import get_sessions_dir
from ohmyself.engine.messages import ConversationMessage


def get_project_session_dir(cwd: str | Path) -> Path:
    path = Path(cwd).resolve()
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:12]
    session_dir = get_sessions_dir() / f"{path.name}-{digest}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def save_session_snapshot(
    *,
    cwd: str | Path,
    model: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str,
    session_started_at: str,
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    session_dir = get_project_session_dir(cwd)
    summary = ""
    for message in messages:
        if message.role == "user" and message.text.strip():
            summary = message.text.strip()[:80]
            break
    payload = {
        "session_id": session_id,
        "session_started_at": session_started_at,
        "cwd": str(Path(cwd).resolve()),
        "model": model,
        "messages": [message.model_dump(mode="json") for message in messages],
        "usage": usage.model_dump(),
        "summary": summary,
        "message_count": len(messages),
        "updated_at": time.time(),
        "tool_metadata": _snapshot_tool_metadata(tool_metadata),
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    latest_path = session_dir / "latest.json"
    latest_path.write_text(data, encoding="utf-8")
    session_path = session_dir / f"session-{session_id}.json"
    session_path.write_text(data, encoding="utf-8")
    return latest_path


def load_latest_session_snapshot(cwd: str | Path) -> dict[str, Any] | None:
    path = get_project_session_dir(cwd) / "latest.json"
    if not path.exists():
        return None
    return _normalize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))


def _normalize_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages", [])
    if isinstance(messages, list):
        payload = dict(payload)
        payload["messages"] = [ConversationMessage.model_validate(item) for item in messages]
        payload["message_count"] = len(payload["messages"])
    usage = payload.get("usage")
    if isinstance(usage, dict):
        payload["usage"] = UsageSnapshot.model_validate(usage)
    return payload


def _snapshot_tool_metadata(tool_metadata: dict[str, object] | None) -> dict[str, Any]:
    if not isinstance(tool_metadata, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in ("active_profile", "active_artifacts", "last_goal", "subagent_runs", "agent_depth", "agent_lineage"):
        if key in tool_metadata:
            payload[key] = _to_jsonable(tool_metadata[key])
    return payload


def _to_jsonable(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    return str(value)
