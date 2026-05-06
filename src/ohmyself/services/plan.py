from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ohmyself.config.paths import get_home_dir


@dataclass(frozen=True)
class PlanEntry:
    entry_id: str
    path: Path
    content: str
    created_at: datetime


def get_plan_dir() -> Path:
    path = get_home_dir() / "plans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_plan_path(for_date: date | None = None) -> Path:
    target = for_date or date.today()
    return get_plan_dir() / f"{target.isoformat()}.md"


def get_plan_inbox_path(for_date: date | None = None) -> Path:
    target = for_date or date.today()
    return get_plan_dir() / f"{target.isoformat()}.inbox.md"


def append_plan(content: str, *, now: datetime | None = None) -> PlanEntry:
    cleaned = content.strip()
    if not cleaned:
        raise ValueError("plan content cannot be empty")
    created_at = now or datetime.now().astimezone()
    entry_id = f"PLAN-{created_at.strftime('%Y%m%d-%H%M%S')}"
    path = get_plan_inbox_path(created_at.date())
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    topic, detail = parse_plan_content(cleaned)
    rendered = f"{topic}：{detail}" if topic else detail
    line = f"- [{created_at.strftime('%H:%M')}] {rendered}"
    separator = "\n" if existing.strip() else ""
    path.write_text(f"{existing.rstrip()}{separator}{line}\n", encoding="utf-8")
    return PlanEntry(entry_id=entry_id, path=get_plan_path(created_at.date()), content=cleaned, created_at=created_at)


def parse_plan_content(content: str) -> tuple[str | None, str]:
    cleaned = content.strip()
    for separator in ("：", ":"):
        if separator in cleaned:
            topic, detail = cleaned.split(separator, 1)
            normalized_topic = topic.strip()
            normalized_detail = detail.strip()
            if normalized_topic and normalized_detail:
                return normalized_topic, normalized_detail
    return None, cleaned


def read_today_plan() -> tuple[str, Path]:
    path = get_plan_path()
    if not path.exists():
        return "", path
    return path.read_text(encoding="utf-8", errors="replace"), path


def read_plan_inbox(for_date: date | None = None) -> tuple[str, Path]:
    path = get_plan_inbox_path(for_date)
    if not path.exists():
        return "", path
    return path.read_text(encoding="utf-8", errors="replace"), path


def has_plan_content(for_date: date | None = None) -> bool:
    path = get_plan_path(for_date)
    if not path.exists():
        return False
    try:
        return bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def has_plan_inbox_content(for_date: date | None = None) -> bool:
    path = get_plan_inbox_path(for_date)
    if not path.exists():
        return False
    try:
        return bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def build_plan_organize_prompt(*, goal_context: str = "", active_goal_count: int = 0, goal_limit: int = 0) -> str:
    today = date.today().isoformat()
    source_path = get_plan_inbox_path()
    target_path = get_plan_path()
    goal_section = goal_context.strip() or "(no active goals)"
    capacity_line = (
        f"Active goal slots used: {active_goal_count}/{goal_limit}."
        if goal_limit > 0
        else f"Active goal count: {active_goal_count}."
    )
    return f"""\
Organize today's plan for {today}.

Source inbox file: {source_path}
Target display file: {target_path}
Active goals:
{goal_section}
{capacity_line}

Instructions:
1. Read the inbox file at `{source_path}`. It contains raw notes captured from `/plan [content]`.
2. Some entries may use the format `topic: detail` or `topic：detail`. If the topic matches an active goal topic, keep that work under the matching goal heading.
3. Entries with the same topic must be grouped together under one shared section instead of being scattered across the plan.
4. Rewrite those notes into a clean daily plan for today.
5. If an item does not match any active goal, decide whether it is short-term or long-term:
   - Short-term items should stay in today's plan.
   - Long-term items should not be written into the daily plan file.
6. If a long-term item is not covered by an active goal and active goals are already full, mention that directly in your reply and explicitly suggest focusing on an existing goal first instead of migrating that item right now.
7. If a long-term item is not covered by an active goal and there is spare goal capacity, mention that directly in your reply and say it may deserve migration into goal tracking.
8. Overwrite `{target_path}` with only the organized daily plan. Do not include long-term non-goal warnings in the file. Do not include raw metadata, timestamps, internal IDs, `created_at`, `source`, or any ingestion scaffolding.
9. The output should read like a usable plan, not a log. Keep it concise and faithful to the user's intent.
10. Prefer a structure such as:
   - `# Daily Plan - {today}`
   - optional sections like `## Focus`, `## In Progress`, `## Next`, `## Notes`
11. If the inbox is empty, write:
   `# Daily Plan - {today}`
   followed by a short line saying there is no plan yet.
12. Use the available file tools to update `{target_path}`.
13. After updating the file, reply with one short sentence. If there are long-term non-goal items, include the warning there instead of putting it in the plan file.
"""
