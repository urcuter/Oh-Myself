from __future__ import annotations

from pathlib import Path

from ohmyself.api.client import ApiMessageCompleteEvent, ApiMessageRequest, SupportsStreamingMessages
from ohmyself.engine.messages import ConversationMessage

_USER_PROFILE_FILENAME = "user_profile.md"
_PROFILE_SYSTEM_PROMPT = """\
You update the persistent user profile for a terminal AI coding assistant.

Write concise markdown for `user_profile.md`.

Rules:
- Use only evidence from the conversation and explicit extra instructions.
- Preserve stable preferences, constraints, working style, language preference, and long-lived project context.
- Omit guesses, temporary task details, and unsupported claims.
- Prefer short bullet lists with clear headings.
- Return only markdown content for the file.
"""


def get_user_profile_path(memory_dir: Path) -> Path:
    return memory_dir / _USER_PROFILE_FILENAME


def load_user_profile(memory_dir: Path) -> str:
    path = get_user_profile_path(memory_dir)
    if not path.exists() or path.is_dir():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_user_profile(memory_dir: Path, content: str) -> Path:
    path = get_user_profile_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.strip()
    path.write_text((normalized + "\n") if normalized else "", encoding="utf-8")
    return path


async def generate_user_profile(
    *,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int,
    conversation: list[ConversationMessage],
    memory_dir: Path,
    extra_instruction: str = "",
) -> str:
    transcript = _conversation_to_profile_source(conversation)
    existing_profile = load_user_profile(memory_dir)
    prompt_parts = []
    if existing_profile:
        prompt_parts.extend(
            [
                "Existing user profile:",
                "```md",
                existing_profile,
                "```",
                "",
            ]
        )
    if extra_instruction.strip():
        prompt_parts.extend(
            [
                "Additional instruction for this update:",
                extra_instruction.strip(),
                "",
            ]
        )
    prompt_parts.extend(
        [
            "Conversation evidence:",
            "```text",
            transcript or "(no conversation text available)",
            "```",
        ]
    )
    request = ApiMessageRequest(
        model=model,
        messages=[ConversationMessage.from_user_text("\n".join(prompt_parts))],
        system_prompt=_PROFILE_SYSTEM_PROMPT,
        max_tokens=max_tokens,
    )
    final_text = ""
    async for event in api_client.stream_message(request):
        if isinstance(event, ApiMessageCompleteEvent):
            final_text = event.message.text.strip()
    if not final_text:
        raise RuntimeError("Model returned an empty user profile.")
    return final_text


def _conversation_to_profile_source(conversation: list[ConversationMessage]) -> str:
    lines: list[str] = []
    for message in conversation:
        text = message.text.strip()
        if text:
            lines.append(f"{message.role}: {text}")
    return "\n\n".join(lines)
