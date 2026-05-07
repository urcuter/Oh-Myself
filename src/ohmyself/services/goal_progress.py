from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ohmyself.config.paths import get_home_dir
from ohmyself.services.goal import GoalEntry, get_goal_dir, list_goals, update_goal_progress

if TYPE_CHECKING:
    from ohmyself.api.client import SupportsStreamingMessages

_DAILY_MARKER_FILENAME = ".last_progress_check"

_DAILY_PROGRESS_SYSTEM_PROMPT = """\
You are a goal progress assessor. Based on yesterday's daily plan, determine the new progress percentage for each goal.

Guidelines:
- Progress can go up (tasks completed) or down (previous estimate was too optimistic).
- If a goal has related tasks in the plan that appear completed, increase progress meaningfully.
- If a goal's tasks are still in-progress or untouched, progress may stay the same or decrease slightly.
- If no tasks relate to a goal, skip that goal — do not include it in the output.
- Progress reflects overall journey to completion, not just plan completion rate.
- Be honest and conservative. It's fine to decrease progress if reality doesn't match earlier estimates.

Return ONLY a JSON array (no markdown wrapping, no extra text):
[
  {"goal_id": "GOAL-...", "new_progress": 30, "note": "Completed task A and B"},
  {"goal_id": "GOAL-...", "new_progress": 60, "note": "All planned work done, entering final phase"}
]"""


def get_daily_marker_path() -> Path:
    return get_goal_dir() / _DAILY_MARKER_FILENAME


def get_last_progress_check_date() -> date | None:
    path = get_daily_marker_path()
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        return date.fromisoformat(text)
    except (ValueError, OSError):
        return None


def set_last_progress_check_date(check_date: date) -> None:
    path = get_daily_marker_path()
    path.write_text(check_date.isoformat() + "\n", encoding="utf-8")


def _build_daily_progress_prompt(
    yesterday: date,
    plan_content: str,
    active_goals: list[GoalEntry],
) -> str:
    goals_lines: list[str] = []
    for goal in active_goals:
        goals_lines.append(
            f"- {goal.entry_id}: {goal.topic} (current progress: {goal.progress_percent}%)"
        )
        if goal.description:
            goals_lines.append(f"  description: {goal.description}")
    goals_text = "\n".join(goals_lines) if goals_lines else "(no active goals)"

    return f"""\
Yesterday's daily plan ({yesterday.isoformat()}):

{plan_content if plan_content.strip() else "(empty — no plan recorded)"}

Active goals:
{goals_text}

For each active goal that has related content in yesterday's plan, assess the new progress percentage and provide a brief note."""


def _parse_progress_json(text: str) -> list[dict]:
    text = text.strip()
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if json_match:
        text = json_match.group(0)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass
    return []


async def assess_daily_goal_progress(
    *,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int,
    yesterday: date,
    plan_content: str,
    active_goals: list[GoalEntry],
) -> list[dict]:
    from ohmyself.api.client import ApiMessageCompleteEvent, ApiMessageRequest
    from ohmyself.engine.messages import ConversationMessage

    if not active_goals or not plan_content.strip():
        return []

    prompt = _build_daily_progress_prompt(yesterday, plan_content, active_goals)
    request = ApiMessageRequest(
        model=model,
        messages=[ConversationMessage.from_user_text(prompt)],
        system_prompt=_DAILY_PROGRESS_SYSTEM_PROMPT,
        max_tokens=max_tokens,
    )
    result_text = ""
    async for event in api_client.stream_message(request):
        if isinstance(event, ApiMessageCompleteEvent):
            result_text = event.message.text.strip()

    if not result_text:
        return []

    return _parse_progress_json(result_text)
