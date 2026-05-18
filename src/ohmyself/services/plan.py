from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ohmyself.config.paths import get_home_dir


def read_text_file_robust(path: Path) -> str:
    raw = path.read_bytes()
    if not raw:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig", errors="replace")
    return raw.decode("utf-8", errors="replace")


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
    existing = read_text_file_robust(path) if path.exists() else ""
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
    return read_text_file_robust(path), path


def read_plan_inbox(for_date: date | None = None) -> tuple[str, Path]:
    path = get_plan_inbox_path(for_date)
    if not path.exists():
        return "", path
    return read_text_file_robust(path), path


def has_plan_content(for_date: date | None = None) -> bool:
    path = get_plan_path(for_date)
    if not path.exists():
        return False
    try:
        return bool(read_text_file_robust(path).strip())
    except OSError:
        return False


def has_plan_inbox_content(for_date: date | None = None) -> bool:
    path = get_plan_inbox_path(for_date)
    if not path.exists():
        return False
    try:
        return bool(read_text_file_robust(path).strip())
    except OSError:
        return False


def build_plan_organize_prompt(*, goal_context: str = "", active_goal_count: int = 0, goal_limit: int = 0, strategy_context: str = "", status_context: str = "", coping_context: str = "", long_plan_context: str = "", daily_context: str = "") -> str:
    today = date.today().isoformat()
    source_path = get_plan_inbox_path()
    target_path = get_plan_path()
    goal_section = goal_context.strip() or "(no active goals)"
    capacity_line = (
        f"Active goal slots used: {active_goal_count}/{goal_limit}."
        if goal_limit > 0
        else f"Active goal count: {active_goal_count}."
    )
    strategy_section = strategy_context.strip() or "(no strategy defined)"
    status_section = status_context.strip() or "(no status recorded)"
    coping_section = coping_context.strip() or "(no coping strategies defined)"
    long_plan_section = long_plan_context.strip() if long_plan_context.strip() else ""
    long_plan_block = f"## Long-term Schedule Plan\n{long_plan_section}\n\n" if long_plan_section else ""
    daily_context_section = daily_context.strip() if daily_context.strip() else ""
    daily_context_block = f"## Daily Context (Learning Focus, Special Situations)\n{daily_context_section}\n\n" if daily_context_section else ""
    return f"""\
Organize today's plan for {today}.

Source inbox file: {source_path}
Target display file: {target_path}

## Long-term Strategy
{strategy_section}

{long_plan_block}{daily_context_block}## Personal Status Context
{status_section}

## Coping Strategies
{coping_section}

## Active goals:
{goal_section}
{capacity_line}

Instructions:
1. Read the inbox file at `{source_path}`. It contains raw notes captured from `/plan [content]`.
2. Some entries may use the format `topic: detail` or `topic：detail`. If the topic matches an active goal topic, keep that work under the matching goal heading.
3. Entries with the same topic must be grouped together under one shared section instead of being scattered across the plan.
4. Rewrite those notes into a clean daily plan for today.
5. Consider the user's current personal status (energy, health, emotions) when prioritizing and organizing tasks — don't overload a low-energy day.
6. Consider the long-term strategy: tasks that align with the strategy should be prioritized.
7. Consider the long-term schedule plan: if there are upcoming milestones this week, ensure they are reflected in today's plan.
8. Consider relevant coping strategies: if the user's status suggests a coping rule applies, add the suggested action to the plan.
9. Consider the user's daily context (learning focus, special situations like interviews or internships): if provided, prioritize tasks that align with the stated learning focus or address special situations.
10. If an item does not match any active goal, decide whether it is short-term or long-term:
   - Short-term items should stay in today's plan.
   - Long-term items should not be written into the daily plan file.
11. If a long-term item is not covered by an active goal and active goals are already full, mention that directly in your reply and explicitly suggest focusing on an existing goal first instead of migrating that item right now.
12. If a long-term item is not covered by an active goal and there is spare goal capacity, mention that directly in your reply and say it may deserve migration into goal tracking.
13. Overwrite `{target_path}` with only the organized daily plan. Do not include long-term non-goal warnings in the file. Do not include raw metadata, timestamps, internal IDs, `created_at`, `source`, or any ingestion scaffolding.
14. The output should read like a usable plan, not a log. Keep it concise and faithful to the user's intent.
15. Prefer a structure such as:
   - `# Daily Plan - {today}`
   - optional sections like `## Focus`, `## In Progress`, `## Next`, `## Notes`
16. If the inbox is empty, write:
   `# Daily Plan - {today}`
   followed by a short line saying there is no plan yet.
17. Use the `write_file` tool (not shell commands) to update `{target_path}`. Always write with UTF-8 encoding.
18. After updating the file, reply with one short sentence. If there are long-term non-goal items, include the warning there instead of putting it in the plan file.
"""
