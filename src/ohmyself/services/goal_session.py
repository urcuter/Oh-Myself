from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ohmyself.services.goal_memory import get_goal_session_dir


def get_session_index_path(goal_id: str) -> Path:
    return get_goal_session_dir(goal_id) / "index.json"


def get_session_summaries_dir(goal_id: str) -> Path:
    path = get_goal_session_dir(goal_id) / "summaries"
    path.mkdir(parents=True, exist_ok=True)
    return path


def link_session_to_goal(
    goal_id: str,
    session_id: str,
    *,
    summary: str = "",
    cwd: str = "",
    model: str = "",
    message_count: int = 0,
    now: datetime | None = None,
) -> None:
    timestamp = now or datetime.now().astimezone()
    index_path = get_session_index_path(goal_id)
    existing: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []

    existing_ids = {entry.get("session_id") for entry in existing}
    if session_id in existing_ids:
        return

    entry = {
        "session_id": session_id,
        "linked_at": timestamp.isoformat(timespec="seconds"),
        "summary": summary[:200] if summary else "",
        "cwd": cwd,
        "model": model,
        "message_count": message_count,
    }
    existing.insert(0, entry)
    if len(existing) > 50:
        existing = existing[:50]

    index_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def list_goal_sessions(goal_id: str) -> list[dict[str, Any]]:
    index_path = get_session_index_path(goal_id)
    if not index_path.exists():
        return []
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_session_summary(goal_id: str, session_id: str, summary: str) -> Path:
    summaries_dir = get_session_summaries_dir(goal_id)
    path = summaries_dir / f"{session_id}.md"
    path.write_text(summary.strip() + "\n", encoding="utf-8")
    return path


def load_session_summary(goal_id: str, session_id: str) -> str:
    path = get_session_summaries_dir(goal_id) / f"{session_id}.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def format_recent_sessions_for_prompt(goal_id: str, limit: int = 3) -> str:
    sessions = list_goal_sessions(goal_id)
    if not sessions:
        return ""

    recent = sessions[:limit]
    lines: list[str] = ["# 最近相关会话"]

    for session in recent:
        sid = session.get("session_id", "unknown")
        session_summary = load_session_summary(goal_id, sid)
        index_summary = session.get("summary", "")

        display_summary = session_summary.strip() or index_summary or "(no summary)"
        if len(display_summary) > 150:
            display_summary = display_summary[:150] + "..."

        linked_at = session.get("linked_at", "")
        lines.append(f"- {sid} ({linked_at}): {display_summary}")

    return "\n".join(lines)
