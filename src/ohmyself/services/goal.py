from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ohmyself.config.paths import get_home_dir

MAX_ACTIVE_GOALS = 5
GOAL_STATUSES = {"active", "completed", "stopped"}


class GoalError(ValueError):
    pass


class GoalLimitError(GoalError):
    pass


class GoalNotFoundError(GoalError):
    pass


@dataclass(frozen=True)
class GoalProgressRecord:
    progress_percent: int
    status: str
    recorded_at: datetime
    event: str
    note: str = ""


@dataclass(frozen=True)
class GoalEntry:
    entry_id: str
    path: Path
    topic: str
    description: str
    created_at: datetime
    progress_percent: int
    ends_at: date | None
    status: str
    updated_at: datetime
    progress_history: tuple[GoalProgressRecord, ...]
    closed_at: datetime | None = None
    linked_dir: str | None = None


def get_goal_dir() -> Path:
    path = get_home_dir() / "goals"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_goal_path() -> Path:
    return get_goal_dir() / "goals.json"


def ensure_goal_dir(goal_id: str) -> Path:
    base = get_goal_dir() / goal_id
    base.mkdir(parents=True, exist_ok=True)
    (base / "memory").mkdir(parents=True, exist_ok=True)
    (base / "experiences").mkdir(parents=True, exist_ok=True)
    (base / "sessions").mkdir(parents=True, exist_ok=True)
    for filename in ("ai_notes.md", "user_prefs.md", "context.md"):
        file_path = base / "memory" / filename
        if not file_path.exists():
            file_path.write_text("", encoding="utf-8")
    exp_path = base / "experiences" / "default.md"
    if not exp_path.exists():
        exp_path.write_text(
            f"# {goal_id} Experience Library\n\n"
            "New experience entries for this goal are appended here first.\n",
            encoding="utf-8",
        )
    return base


def append_goal(
    topic: str,
    *,
    description: str = "",
    ends_at: date | str | None = None,
    progress_percent: int = 0,
    linked_dir: str | None = None,
    now: datetime | None = None,
    max_active_goals: int = MAX_ACTIVE_GOALS,
) -> GoalEntry:
    cleaned_topic, cleaned_description = _normalize_topic_description(topic, description)
    if not cleaned_topic:
        raise GoalError("goal topic cannot be empty")
    if not 0 <= progress_percent <= 100:
        raise GoalError("goal progress must be between 0 and 100")

    goals = list_goals()
    active_count = sum(1 for goal in goals if goal.status == "active")
    if active_count >= max_active_goals:
        raise GoalLimitError(f"active goal limit reached: {max_active_goals}")

    created_at = now or datetime.now().astimezone()
    normalized_ends_at = _normalize_date(ends_at)
    status = "completed" if progress_percent == 100 else "active"
    closed_at = created_at if status == "completed" else None
    entry_id = f"GOAL-{created_at.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    entry = GoalEntry(
        entry_id=entry_id,
        path=get_goal_path(),
        topic=cleaned_topic,
        description=cleaned_description,
        created_at=created_at,
        progress_percent=progress_percent,
        ends_at=normalized_ends_at,
        status=status,
        updated_at=created_at,
        progress_history=(
            GoalProgressRecord(
                progress_percent=progress_percent,
                status=status,
                recorded_at=created_at,
                event="created",
            ),
        ),
        closed_at=closed_at,
        linked_dir=linked_dir,
    )
    ensure_goal_dir(entry_id)
    goals.append(entry)
    _write_goals(goals)
    return entry


def list_goals() -> list[GoalEntry]:
    payload = _read_payload()
    items = payload.get("goals", [])
    if not isinstance(items, list):
        return []
    goals: list[GoalEntry] = []
    for item in items:
        if isinstance(item, dict):
            try:
                goals.append(_goal_from_payload(item))
            except (KeyError, TypeError, ValueError):
                continue
    return goals


def has_goal_content() -> bool:
    return bool(list_goals())


def update_goal_progress(entry_id: str, progress_percent: int, *, note: str = "", now: datetime | None = None) -> GoalEntry:
    if not 0 <= progress_percent <= 100:
        raise GoalError("goal progress must be between 0 and 100")
    timestamp = now or datetime.now().astimezone()

    def update(goal: GoalEntry) -> GoalEntry:
        status = "completed" if progress_percent == 100 else goal.status
        closed_at = timestamp if status == "completed" and goal.closed_at is None else goal.closed_at
        return _replace_goal(
            goal,
            progress_percent=progress_percent,
            status=status,
            updated_at=timestamp,
            closed_at=closed_at,
            progress_history=_append_progress_record(goal, progress_percent, status, timestamp, "progress", note=note),
        )

    return _update_goal(entry_id, update)


def complete_goal(entry_id: str, *, now: datetime | None = None) -> GoalEntry:
    timestamp = now or datetime.now().astimezone()

    def update(goal: GoalEntry) -> GoalEntry:
        return _replace_goal(
            goal,
            progress_percent=100,
            status="completed",
            updated_at=timestamp,
            closed_at=timestamp,
            progress_history=_append_progress_record(goal, 100, "completed", timestamp, "completed"),
        )

    return _update_goal(entry_id, update)


def stop_goal(entry_id: str, *, now: datetime | None = None) -> GoalEntry:
    timestamp = now or datetime.now().astimezone()

    def update(goal: GoalEntry) -> GoalEntry:
        return _replace_goal(
            goal,
            status="stopped",
            updated_at=timestamp,
            closed_at=timestamp,
            progress_history=_append_progress_record(goal, goal.progress_percent, "stopped", timestamp, "stopped"),
        )

    return _update_goal(entry_id, update)


def set_goal_linked_dir(entry_id: str, linked_dir: str | None) -> GoalEntry:
    def update(goal: GoalEntry) -> GoalEntry:
        return _replace_goal(goal, linked_dir=linked_dir, updated_at=datetime.now().astimezone())

    return _update_goal(entry_id, update)


def format_goals_markdown(goals: list[GoalEntry] | None = None) -> str:
    entries = goals if goals is not None else list_goals()
    if not entries:
        return "No goals yet. Use `/goal [topic] --desc \"...\" --ends YYYY-MM-DD --dir <path>` to create one."

    groups = [
        ("Active Goals", [goal for goal in entries if goal.status == "active"]),
        ("Completed Goals", [goal for goal in entries if goal.status == "completed"]),
        ("Stopped Goals", [goal for goal in entries if goal.status == "stopped"]),
    ]
    lines: list[str] = ["# Goals"]
    for title, group in groups:
        if not group:
            continue
        lines.extend(["", f"## {title}"])
        for goal in sorted(group, key=lambda item: item.created_at):
            lines.append(f"- `{goal.entry_id}` {goal.progress_percent}% ends {goal.ends_at.isoformat() if goal.ends_at else 'unset'}")
            lines.append(f"  {goal.topic}")
            if goal.description:
                lines.append(f"  {goal.description}")
            if goal.linked_dir:
                lines.append(f"  linked_dir: {goal.linked_dir}")
    return "\n".join(lines)


def _update_goal(entry_id: str, updater) -> GoalEntry:
    goals = list_goals()
    updated: GoalEntry | None = None
    next_goals: list[GoalEntry] = []
    for goal in goals:
        if goal.entry_id == entry_id:
            updated = updater(goal)
            next_goals.append(updated)
        else:
            next_goals.append(goal)
    if updated is None:
        raise GoalNotFoundError(f"goal not found: {entry_id}")
    _write_goals(next_goals)
    return updated


def _replace_goal(goal: GoalEntry, **updates: object) -> GoalEntry:
    values = {
        "entry_id": goal.entry_id,
        "path": goal.path,
        "topic": goal.topic,
        "description": goal.description,
        "created_at": goal.created_at,
        "progress_percent": goal.progress_percent,
        "ends_at": goal.ends_at,
        "status": goal.status,
        "updated_at": goal.updated_at,
        "progress_history": goal.progress_history,
        "closed_at": goal.closed_at,
        "linked_dir": goal.linked_dir,
    }
    values.update(updates)
    return GoalEntry(**values)  # type: ignore[arg-type]


def _read_payload() -> dict[str, Any]:
    path = get_goal_path()
    if not path.exists():
        return {"goals": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_goals(goals: list[GoalEntry]) -> None:
    path = get_goal_path()
    payload = {"goals": [_goal_to_payload(goal) for goal in goals]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _goal_to_payload(goal: GoalEntry) -> dict[str, Any]:
    payload = {
        "entry_id": goal.entry_id,
        "topic": goal.topic,
        "description": goal.description,
        "created_at": goal.created_at.isoformat(timespec="seconds"),
        "progress_percent": goal.progress_percent,
        "ends_at": goal.ends_at.isoformat() if goal.ends_at else None,
        "status": goal.status,
        "updated_at": goal.updated_at.isoformat(timespec="seconds"),
        "progress_history": [_progress_record_to_payload(record) for record in goal.progress_history],
        "closed_at": goal.closed_at.isoformat(timespec="seconds") if goal.closed_at else None,
    }
    if goal.linked_dir:
        payload["linked_dir"] = goal.linked_dir
    return payload


def _goal_from_payload(payload: dict[str, Any]) -> GoalEntry:
    status = str(payload["status"])
    if status not in GOAL_STATUSES:
        raise ValueError(f"unknown goal status: {status}")
    created_at = datetime.fromisoformat(str(payload["created_at"]))
    progress_percent = int(payload["progress_percent"])
    updated_at = datetime.fromisoformat(str(payload["updated_at"]))
    topic, description = _normalize_topic_description(
        str(payload.get("topic") or payload.get("content") or "").strip(),
        str(payload.get("description") or "").strip(),
    )
    if not topic:
        raise ValueError("goal topic cannot be empty")
    return GoalEntry(
        entry_id=str(payload["entry_id"]),
        path=get_goal_path(),
        topic=topic,
        description=description,
        created_at=created_at,
        progress_percent=progress_percent,
        ends_at=_normalize_date(payload.get("ends_at")),
        status=status,
        updated_at=updated_at,
        progress_history=_normalize_progress_history(
            payload.get("progress_history"),
            fallback_progress_percent=progress_percent,
            fallback_status=status,
            fallback_recorded_at=updated_at or created_at,
        ),
        closed_at=_normalize_datetime(payload.get("closed_at")),
        linked_dir=payload.get("linked_dir") or None,
    )


def _append_progress_record(
    goal: GoalEntry,
    progress_percent: int,
    status: str,
    recorded_at: datetime,
    event: str,
    note: str = "",
) -> tuple[GoalProgressRecord, ...]:
    return (
        *goal.progress_history,
        GoalProgressRecord(
            progress_percent=progress_percent,
            status=status,
            recorded_at=recorded_at,
            event=event,
            note=note,
        ),
    )


def _progress_record_to_payload(record: GoalProgressRecord) -> dict[str, Any]:
    payload = {
        "progress_percent": record.progress_percent,
        "status": record.status,
        "recorded_at": record.recorded_at.isoformat(timespec="seconds"),
        "event": record.event,
    }
    if record.note:
        payload["note"] = record.note
    return payload


def _normalize_progress_history(
    value: object,
    *,
    fallback_progress_percent: int,
    fallback_status: str,
    fallback_recorded_at: datetime,
) -> tuple[GoalProgressRecord, ...]:
    if not isinstance(value, list):
        return (
            GoalProgressRecord(
                progress_percent=fallback_progress_percent,
                status=fallback_status,
                recorded_at=fallback_recorded_at,
                event="imported",
            ),
        )
    records: list[GoalProgressRecord] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            record_status = str(item["status"])
            if record_status not in GOAL_STATUSES:
                continue
            records.append(
                GoalProgressRecord(
                    progress_percent=int(item["progress_percent"]),
                    status=record_status,
                    recorded_at=datetime.fromisoformat(str(item["recorded_at"])),
                    event=str(item.get("event") or "progress"),
                    note=str(item.get("note") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    if records:
        return tuple(records)
    return (
        GoalProgressRecord(
            progress_percent=fallback_progress_percent,
            status=fallback_status,
            recorded_at=fallback_recorded_at,
            event="imported",
        ),
    )


def _normalize_date(value: date | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _normalize_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _normalize_topic_description(topic: str, description: str) -> tuple[str, str]:
    cleaned_topic = topic.strip()
    cleaned_description = description.strip()
    if cleaned_description:
        return cleaned_topic, cleaned_description
    for separator in ("：", ":"):
        if separator in cleaned_topic:
            left, right = cleaned_topic.split(separator, 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                return left, right
    if " --" in cleaned_topic:
        left, right = cleaned_topic.split(" --", 1)
        left = left.strip()
        right = right.strip(" -")
        if left and right:
            return left, right
    return cleaned_topic, cleaned_description
