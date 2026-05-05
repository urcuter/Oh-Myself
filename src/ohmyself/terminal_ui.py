from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

ACCENT = "#e58a61"
MUTED = "grey62"
BORDER = "grey35"
SUCCESS = "#7cb66d"
ERROR = "#d96c6c"
BLUE = "#3b82f6"
LIGHT_BLUE = "#6f86a8"
RED = "#ef4444"
INFO = BLUE
MODEL_MUTED = "grey50"
LOGO_DARK = "grey54"
LOGO_LIGHT = "grey86"

_CONSOLE = Console(highlight=True, soft_wrap=True)
EVENT_INDENT = "      "
ASSISTANT_INDENT = "    "
EVENT_PREFIX = "| "


@dataclass(frozen=True)
class RestoredSessionSummary:
    session_id: str
    message_count: int
    summary: str = ""
    updated_at: float | None = None


def console() -> Console:
    return _CONSOLE


def print_markdown(text: str) -> None:
    _CONSOLE.print(_padded_md(text))


def prompt_text(*, model_name: str) -> Text:
    prompt = Text()
    prompt.append("  ")
    prompt.append("ohmy", style=f"bold {ACCENT}")
    prompt.append("[", style=MUTED)
    prompt.append(_short_model_name(model_name), style=MODEL_MUTED)
    prompt.append("]", style=MUTED)
    prompt.append(" > ", style=MUTED)
    return prompt


def print_status(message: str) -> None:
    label = Text("status", style=f"bold {ACCENT}")
    body = Text.assemble(EVENT_INDENT, "[", label, ("] ", MUTED), message)
    _CONSOLE.print(body)


def print_error(message: str) -> None:
    label = Text("error", style=f"bold {ERROR}")
    body = Text.assemble(EVENT_INDENT, "[", label, ("] ", MUTED), message)
    _CONSOLE.print(body)


def print_success(message: str) -> None:
    label = Text("done", style=f"bold {SUCCESS}")
    body = Text.assemble(EVENT_INDENT, "[", label, ("] ", MUTED), message)
    _CONSOLE.print(body)


def format_assistant_chunk(text: str, *, line_start: bool, first_line: bool) -> tuple[str, bool, bool]:
    if not text:
        return "", line_start, first_line
    parts = text.splitlines(keepends=True)
    rendered: list[str] = []
    current_line_start = line_start
    current_first_line = first_line
    for part in parts:
        if current_line_start:
            rendered.append(ASSISTANT_INDENT)
        rendered.append(part)
        current_line_start = part.endswith("\n")
        if current_line_start:
            current_first_line = False
    return "".join(rendered), current_line_start, current_first_line


def indent_block(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return ASSISTANT_INDENT + text
    rendered: list[str] = []
    for line in lines:
        rendered.append(f"{ASSISTANT_INDENT}{line}" if line else "")
    return "\n".join(rendered)


def print_tool_started(tool_name: str, preview: str) -> None:
    label = Text("tool", style=f"bold {INFO}")
    body = Text.assemble(
        EVENT_INDENT,
        EVENT_PREFIX,
        "[",
        label,
        ("] ", MUTED),
        (tool_name, "bold"),
        " ",
    )
    _CONSOLE.print(body)
    if preview:
        _CONSOLE.print(Text.assemble(EVENT_INDENT, EVENT_PREFIX, ("  args ", MUTED), (preview, MUTED)))


def print_tool_completed(tool_name: str, *, is_error: bool, detail: str | None = None) -> None:
    if is_error:
        label = Text("tool:error", style=f"bold {ERROR}")
        message = detail or "(no output)"
    else:
        label = Text("tool:done", style=f"bold {SUCCESS}")
        message = tool_name
    body = Text.assemble(EVENT_INDENT, EVENT_PREFIX, "[", label, ("] ", MUTED), message)
    _CONSOLE.print(body)


def print_help_panel() -> None:
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column(style=f"bold {ACCENT}", no_wrap=True)
    table.add_column(style="default")
    table.add_row("/help", "Show local commands")
    table.add_row("/tools", "List enabled tools")
    table.add_row("/status", "Show active profile, model, and permission mode")
    table.add_row("/user_profile [prompt]", "Refresh user_profile.md from the current conversation")
    table.add_row("/clear", "Clear in-memory conversation history")
    table.add_row("/continue", "Continue a paused tool loop")
    table.add_row("/exit", "Exit Oh Myself")
    _CONSOLE.print(
        Panel(
            table,
            title=Text("Local Commands", style=f"bold {ACCENT}"),
            title_align="left",
            border_style=BORDER,
            box=box.ROUNDED,
        )
    )


def print_tools_panel(items: list[tuple[str, str]]) -> None:
    table = Table(box=None, show_header=True, expand=True, padding=(0, 1))
    table.add_column("Tool", style=f"bold {ACCENT}", no_wrap=True)
    table.add_column("Description", style="default")
    for name, description in items:
        table.add_row(name, description)
    _CONSOLE.print(
        Panel(
            table,
            title=Text("Enabled Tools", style=f"bold {ACCENT}"),
            title_align="left",
            border_style=BORDER,
            box=box.ROUNDED,
        )
    )


def print_status_panel(rows: list[tuple[str, str]]) -> None:
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column(style=MUTED, no_wrap=True)
    table.add_column(style="default")
    for key, value in rows:
        table.add_row(key, value)
    _CONSOLE.print(
        Panel(
            table,
            title=Text("Environment", style=f"bold {ACCENT}"),
            title_align="left",
            border_style=BORDER,
            box=box.ROUNDED,
        )
    )


def prompt_permission(tool_name: str, reason: str) -> bool:
    _CONSOLE.print(Text.assemble(EVENT_INDENT, EVENT_PREFIX, ("[permission] ", MUTED), (tool_name, "bold")))
    for line in reason.splitlines():
        _CONSOLE.print(Text.assemble(EVENT_INDENT, EVENT_PREFIX, (line, MUTED)))
    return Confirm.ask(f"{EVENT_INDENT}{EVENT_PREFIX}Allow this tool call?", console=_CONSOLE, default=False)


def print_welcome(
    *,
    cwd: str,
    profile_name: str,
    profile_label: str,
    model: str,
    permission_mode: str,
    tool_count: int,
    restored: RestoredSessionSummary | None,
) -> None:
    _CONSOLE.print()
    for line in _logo_lines():
        _CONSOLE.print(line)
    _CONSOLE.print(Text("  " + "─" * max(49, min(_CONSOLE.size.width - 4, 65)), style=LIGHT_BLUE))
    _CONSOLE.print()


def _kv_row(label: str, value: str) -> Text:
    return Text.assemble((f"{label:<11}", MUTED), value)


def _shorten_path(value: str) -> str:
    path = Path(value)
    home = Path.home()
    try:
        return str(path).replace(str(home), "~", 1)
    except OSError:
        return value


def _truncate(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def format_relative_time(timestamp: float) -> str:
    now = datetime.now().astimezone().timestamp()
    delta = max(0, int(now - timestamp))
    if delta < 60:
        return "just now"
    if delta < 3600:
        minutes = delta // 60
        return f"{minutes}m ago"
    if delta < 86400:
        hours = delta // 3600
        return f"{hours}h ago"
    days = delta // 86400
    return f"{days}d ago"


def _short_model_name(value: str) -> str:
    compact = value.strip()
    if len(compact) <= 24:
        return compact
    return compact[:24]


_MD_INDENT = 4  # left-pad columns for assistant Markdown output


def _padded_md(text: str) -> Padding:
    return Padding(Markdown(text or " "), pad=(0, 0, 0, _MD_INDENT))


def make_live_markdown(text: str) -> Live:
    """Return a Rich Live context that renders *text* as indented Markdown.

    The caller is responsible for starting/stopping the Live object and for
    calling live.update(_padded_md(new_text)) as more text arrives.
    """
    return Live(_padded_md(text), console=_CONSOLE, refresh_per_second=15, vertical_overflow="visible")


def update_live_markdown(live: Live, text: str) -> None:
    """Update a running Live block with new Markdown text."""
    live.update(_padded_md(text))


def _logo_lines() -> list[Text]:
    dark = LOGO_DARK
    light = LOGO_LIGHT
    oh = [
        "   ___    _        ",
        "  / _ \\  | |       ",
        " | | | | | |__     ",
        " | |_| | | '_ \\    ",
        "  \\___/  | | | |   ",
        "         |_| |_|   ",
    ]
    myself = [
        " __  __                     _  __ ",
        "|  \\/  | _   _  ___   ___  | |/ _|",
        "| |\\/| || | | |/ __| / _ \\ | | |_ ",
        "| |  | || |_| |\\__ \\|  __/ | |  _|",
        "|_|  |_| \\__, ||___/ \\___| |_|_|  ",
        "         |___/                    ",
    ]
    lines: list[Text] = []
    for left, right in zip(oh, myself):
        text = Text()
        text.append("  ")
        text.append(left, style=f"bold {dark}")
        text.append("  ")
        text.append(right, style=f"bold {light}")
        lines.append(text)
    return lines
