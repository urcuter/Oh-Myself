from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ohmyself.config.paths import get_home_dir
from ohmyself.services.goal import get_goal_dir, GoalEntry

if TYPE_CHECKING:
    from ohmyself.api.client import SupportsStreamingMessages
    from ohmyself.engine.messages import ConversationMessage

AI_NOTES_FILENAME = "ai_notes.md"
USER_PREFS_FILENAME = "user_prefs.md"
CONTEXT_FILENAME = "context.md"

_UPDATE_SYSTEM_PROMPT = """\
You update the long-term memory for a specific goal that the user is working on.

Write concise markdown updates for the goal's memory files.

Rules:
- Use only evidence from the conversation and the existing memory content.
- Extract: key progress, decisions made, lessons learned, user preferences, and important context.
- Preserve stable information; do not remove existing content unless it is contradicted by new evidence.
- Keep entries concise with clear headings and bullet points.
- Only write content that the conversation supports — do not invent or speculate.
"""

AI_NOTES_FILENAME = "ai_notes.md"
USER_PREFS_FILENAME = "user_prefs.md"
CONTEXT_FILENAME = "context.md"


def get_goal_memory_dir(goal_id: str) -> Path:
    path = get_goal_dir() / goal_id / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_goal_memory_dirs(goal_id: str) -> Path:
    memory_dir = get_goal_memory_dir(goal_id)
    for filename in (AI_NOTES_FILENAME, USER_PREFS_FILENAME, CONTEXT_FILENAME):
        file_path = memory_dir / filename
        if not file_path.exists():
            file_path.write_text("", encoding="utf-8")
    return memory_dir


def read_goal_memory(goal_id: str, filename: str) -> str:
    path = get_goal_memory_dir(goal_id) / filename
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def append_goal_memory(goal_id: str, filename: str, content: str) -> None:
    cleaned = content.strip()
    if not cleaned:
        return
    ensure_goal_memory_dirs(goal_id)
    path = get_goal_memory_dir(goal_id) / filename
    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    separator = "\n\n" if existing else ""
    path.write_text(f"{existing}{separator}{cleaned}\n", encoding="utf-8")


def format_goal_memory_for_prompt(goal_id: str) -> str:
    ai_notes = read_goal_memory(goal_id, AI_NOTES_FILENAME)
    user_prefs = read_goal_memory(goal_id, USER_PREFS_FILENAME)
    context = read_goal_memory(goal_id, CONTEXT_FILENAME)

    sections: list[str] = []

    if ai_notes:
        notes_lines = ai_notes.splitlines()
        recent_notes = notes_lines[-30:] if len(notes_lines) > 30 else notes_lines
        sections.append("# 目标 AI 笔记\n" + "\n".join(recent_notes))

    if user_prefs:
        prefs_lines = user_prefs.splitlines()
        recent_prefs = prefs_lines[-20:] if len(prefs_lines) > 20 else prefs_lines
        sections.append("# 目标用户偏好\n" + "\n".join(recent_prefs))

    if context:
        sections.append("# 目标上下文\n" + context)

    return "\n\n".join(sections)


def get_goal_experience_dir(goal_id: str) -> Path:
    path = get_goal_dir() / goal_id / "experiences"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_goal_experience_library(goal_id: str) -> Path:
    path = get_goal_experience_dir(goal_id) / "default.md"
    if not path.exists():
        path.write_text(
            f"# {goal_id} Experience Library\n\n"
            "New experience entries for this goal are appended here first.\n",
            encoding="utf-8",
        )
    return path


def get_goal_session_dir(goal_id: str) -> Path:
    path = get_goal_dir() / goal_id / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _conversation_to_text(messages: list[ConversationMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        text = message.text.strip()
        if text:
            lines.append(f"{message.role}: {text}")
    return "\n\n".join(lines)


async def update_goal_memory_via_ai(
    goal_id: str,
    *,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int,
    conversation: list[ConversationMessage],
    goal_entry: GoalEntry,
) -> dict[str, str]:
    from ohmyself.api.client import ApiMessageCompleteEvent, ApiMessageRequest
    from ohmyself.engine.messages import ConversationMessage

    ensure_goal_memory_dirs(goal_id)
    conversation_text = _conversation_to_text(conversation)
    existing_ai_notes = read_goal_memory(goal_id, AI_NOTES_FILENAME)
    existing_user_prefs = read_goal_memory(goal_id, USER_PREFS_FILENAME)
    existing_context = read_goal_memory(goal_id, CONTEXT_FILENAME)

    prompt_parts = [
        f"Goal: {goal_entry.topic}",
        f"Description: {goal_entry.description or '(none)'}",
        f"Progress: {goal_entry.progress_percent}%",
        "",
    ]

    if existing_ai_notes:
        prompt_parts.extend(["Existing AI Notes:", "```md", existing_ai_notes, "```", ""])
    if existing_user_prefs:
        prompt_parts.extend(["Existing User Preferences:", "```md", existing_user_prefs, "```", ""])
    if existing_context:
        prompt_parts.extend(["Existing Context:", "```md", existing_context, "```", ""])

    prompt_parts.extend(
        [
            "Latest conversation:",
            "```text",
            conversation_text or "(empty)",
            "```",
            "",
            "Please analyze the conversation and produce updates in the following format.",
            "Return exactly three sections separated by '---' markers:",
            "",
            "Section 1: Updates to AI Notes (ai_notes.md) — new progress, decisions, lessons.",
            "Section 2: Updates to User Preferences (user_prefs.md) — learned preferences, constraints.",
            "Section 3: Updates to Context (context.md) — important facts, project state.",
            "",
            "Each section should contain ONLY the new content to append (not the full file).",
            "If a section has no new content, write 'NONE'.",
        ]
    )

    request = ApiMessageRequest(
        model=model,
        messages=[ConversationMessage.from_user_text("\n".join(prompt_parts))],
        system_prompt=_UPDATE_SYSTEM_PROMPT,
        max_tokens=max_tokens,
    )
    result_text = ""
    async for event in api_client.stream_message(request):
        if isinstance(event, ApiMessageCompleteEvent):
            result_text = event.message.text.strip()

    if not result_text:
        raise RuntimeError("Model returned empty memory update.")

    sections = result_text.split("---")
    updates: dict[str, str] = {}

    section_files = [AI_NOTES_FILENAME, USER_PREFS_FILENAME, CONTEXT_FILENAME]
    for i, filename in enumerate(section_files):
        if i < len(sections):
            content = sections[i].strip()
            if content and content.upper() != "NONE":
                append_goal_memory(goal_id, filename, content)
                updates[filename] = content

    return updates
