from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import Iterable

from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text
from prompt_toolkit.completion import Completer, Completion

ACCENT = "#e58a61"
WARM_BORDER = "#b8794f"
WARM_TITLE = "#f0a36d"
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
LOCAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/", "Show this command list"),
    ("/help", "Show local commands"),
    ("/tools", "List enabled tools"),
    ("/status", "Show active profile, model, and permission mode"),
    ("/restore", "Restore the latest saved conversation for this workspace"),
    ("/user_profile [prompt]", "Refresh user_profile.md from the current conversation"),
    ("/exper add [content]", "Add a life experience to the local experience library"),
    ("/exper [question]", "Answer by retrieving relevant local life experiences"),
    ("/exper organize", "Classify default experience entries into topic files"),
    ("/goal [topic]", "Show goals, or add one with optional --desc and --ends"),
    ("/goal switch [id]", "Switch to a goal's agent context"),
    ("/goal exit", "Exit goal mode back to general agent"),
    ("/goal memory", "View current goal's memory files"),
    ("/goal memory update", "AI analyzes conversation and updates goal memory"),
    ("/goal memory search [query]", "Search goal memory and experiences (--deep for sessions)"),
    ("/goal sessions", "View sessions linked to current goal"),
    ("/goal progress [id] [0-100]", "Update goal progress"),
    ("/goal done [id]", "Mark a goal completed"),
    ("/goal stop [id]", "Stop a goal"),
    ("/plan [content]", "Show today's organized plan, or add content and auto-organize it"),
    ("/clear", "Clear in-memory conversation history"),
    ("/continue", "Continue a paused tool loop"),
    ("/exit", "Exit Oh Myself"),
)
GOAL_CYCLE_SENTINEL = "\x00GC\x00"


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


def prompt_text(*, model_name: str, goal_topic: str = "") -> Text:
    prompt = Text()
    prompt.append("  ")
    prompt.append("ohmy", style=f"bold {ACCENT}")
    if goal_topic:
        prompt.append(" [", style=MUTED)
        prompt.append(goal_topic[:15], style=ACCENT)
        prompt.append("]", style=MUTED)
    prompt.append("[", style=MUTED)
    prompt.append(_short_model_name(model_name), style=MODEL_MUTED)
    prompt.append("]", style=MUTED)
    prompt.append(" > ", style=MUTED)
    return prompt


def prompt_input(*, model_name: str, plan_topics: Iterable[str] | None = None, goal_context=None, goal_topic: str = "") -> str:
    if _should_use_interactive_prompt():
        try:
            return _prompt_toolkit_input(model_name=model_name, plan_topics=plan_topics, goal_context=goal_context, goal_topic=goal_topic)
        except Exception:
            pass
    return _CONSOLE.input(prompt_text(model_name=model_name, goal_topic=goal_topic))


def print_status(message: str) -> None:
    label = Text("status", style=f"bold {ACCENT}")
    body = Text.assemble(EVENT_INDENT, "[", label, ("] ", MUTED), message)
    _print_notice(body)


def print_error(message: str) -> None:
    label = Text("error", style=f"bold {ERROR}")
    body = Text.assemble(EVENT_INDENT, "[", label, ("] ", MUTED), message)
    _print_notice(body)


def print_success(message: str) -> None:
    label = Text("done", style=f"bold {SUCCESS}")
    body = Text.assemble(EVENT_INDENT, "[", label, ("] ", MUTED), message)
    _print_notice(body)


def _print_notice(body: Text) -> None:
    _CONSOLE.print()
    _CONSOLE.print(body)
    _CONSOLE.print()


def print_context_snapshot(message: str, *, title: str, markdown: str) -> None:
    panel = _context_snapshot_panel(title=title, markdown=markdown)
    print_status(message)
    if _CONSOLE.size.width < 96:
        _CONSOLE.print(Padding(panel, pad=(0, 0, 0, _MD_INDENT)))
        _CONSOLE.print()
        return

    right_width = max(38, min(58, _CONSOLE.size.width // 2))
    panel.width = right_width
    _CONSOLE.print(Align.right(panel))
    _CONSOLE.print()


def _context_snapshot_panel(*, title: str, markdown: str) -> Panel:
    return Panel(
        Markdown(markdown or " "),
        title=Text(title, style=f"bold {WARM_TITLE}"),
        title_align="left",
        border_style=WARM_BORDER,
        box=box.ROUNDED,
        padding=(0, 1),
    )


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


def print_subagent_started(role: str, task: str, *, read_only: bool) -> None:
    label = Text("subagent", style=f"bold {INFO}")
    mode = "read-only" if read_only else "write"
    body = Text.assemble(
        EVENT_INDENT,
        EVENT_PREFIX,
        "[",
        label,
        ("] ", MUTED),
        (role or "specialist", "bold"),
        (" ", MUTED),
        (f"({mode})", MUTED),
    )
    _CONSOLE.print(body)
    if task:
        _CONSOLE.print(Text.assemble(EVENT_INDENT, EVENT_PREFIX, ("  task ", MUTED), (_truncate(task, 120), MUTED)))


def print_subagent_completed(role: str, summary: str, *, session_id: str, is_error: bool, timed_out: bool) -> None:
    if is_error:
        label = Text("subagent:error", style=f"bold {ERROR}")
    else:
        label = Text("subagent:done", style=f"bold {SUCCESS}")
    suffix = " timed out" if timed_out else ""
    title = f"{role or 'specialist'}{suffix}"
    if session_id:
        title += f" [{session_id}]"
    body = Text.assemble(EVENT_INDENT, EVENT_PREFIX, "[", label, ("] ", MUTED), title)
    _CONSOLE.print(body)
    if summary:
        _CONSOLE.print(Text.assemble(EVENT_INDENT, EVENT_PREFIX, ("  summary ", MUTED), (_truncate(summary, 160), MUTED)))


def print_goal_switch_feedback(topic: str | None) -> None:
    _CONSOLE.print()
    if topic:
        label = Text("goal", style=f"bold {ACCENT}")
        body = Text.assemble(
            EVENT_INDENT, "[", label, ("] ", MUTED),
            "已切换到目标: ", (topic, f"bold {ACCENT}"),
        )
    else:
        label = Text("goal", style=f"bold {ACCENT}")
        body = Text.assemble(
            EVENT_INDENT, "[", label, ("] ", MUTED),
            "已退出目标模式",
        )
    _CONSOLE.print(body)
    _CONSOLE.print()


def prompt_goal_memory_update() -> bool:
    """Ask user whether to update goal memory before leaving."""
    _CONSOLE.print()
    return Confirm.ask(
        f"{EVENT_INDENT}当前对话有新的内容，是否更新目标记忆？",
        console=_CONSOLE,
        default=True,
    )

def print_help_panel() -> None:
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column(style=f"bold {ACCENT}", no_wrap=True)
    table.add_column(style="default")
    for command, description in LOCAL_COMMANDS:
        table.add_row(Text(command, style=f"bold {ACCENT}"), description)
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
    _CONSOLE.print(Text(_welcome_divider(_CONSOLE.size.width), style=LIGHT_BLUE))
    _CONSOLE.print()


def _welcome_divider(width: int) -> str:
    return "  " + "-" * max(49, min(width - 4, 65))


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


def supports_live_markdown() -> bool:
    return bool(getattr(_CONSOLE, "is_terminal", False) and getattr(sys.stdout, "isatty", lambda: False)())


def _should_use_interactive_prompt() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stdout, "isatty", lambda: False)())


def _prompt_toolkit_input(*, model_name: str, plan_topics: Iterable[str] | None = None, goal_context=None, goal_topic: str = "") -> str:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.application import get_app

    prompt = ANSI(_ansi_prompt(model_name=model_name, goal_topic=goal_topic))

    kb: KeyBindings | None = None
    if goal_context is not None:

        @Condition
        def buffer_empty() -> bool:
            try:
                app = get_app()
                return not app.current_buffer.text.strip()
            except Exception:
                return False

        kb = KeyBindings()

        @kb.add("tab", filter=buffer_empty)
        def _(event: object) -> None:
            del event
            goal_context.cycle_next()
            get_app().exit(result=GOAL_CYCLE_SENTINEL)

    goal_ids: list[str] = []
    if goal_context is not None:
        goal_context.refresh_goals()
        goal_ids = [g.entry_id for g in goal_context.available_goals]

    session = PromptSession(
        completer=_SlashCommandCompleter(LOCAL_COMMANDS, plan_topics=plan_topics, goal_ids=goal_ids),
        complete_while_typing=True,
        key_bindings=kb,
    )
    return session.prompt(prompt)


class _SlashCommandCompleter(Completer):
    def __init__(
        self,
        commands: Iterable[tuple[str, str]],
        *,
        plan_topics: Iterable[str] | None = None,
        goal_ids: Iterable[str] | None = None,
    ) -> None:
        self._commands = tuple(commands)
        self._plan_topics = tuple(dict.fromkeys(topic.strip() for topic in (plan_topics or ()) if topic and topic.strip()))
        self._goal_ids = tuple(dict.fromkeys(gid.strip() for gid in (goal_ids or ()) if gid and gid.strip()))

    def get_completions(self, document: "Document", complete_event: "CompleteEvent"):
        del complete_event
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        replacement_start = -len(text)

        if text.startswith("/goal switch "):
            prefix = text[len("/goal switch "):]
            for gid in self._goal_ids:
                candidate = f"/goal switch {gid}"
                if candidate.startswith(text):
                    yield Completion(
                        candidate,
                        start_position=replacement_start,
                        display=candidate,
                        display_meta="Switch to this goal",
                    )

        if text.startswith("/plan "):
            for topic in self._plan_topics:
                candidate = f"/plan {topic}\uFF1A"
                if candidate.startswith(text):
                    yield Completion(
                        candidate,
                        start_position=replacement_start,
                        display=candidate,
                        display_meta="Active goal topic",
                    )

        for command, description in self._commands:
            insert_text = command.split(" [", 1)[0]
            if insert_text.startswith(text) or command.startswith(text):
                yield Completion(
                    insert_text,
                    start_position=replacement_start,
                    display=command,
                    display_meta=description,
                )


def _ansi_prompt(*, model_name: str, goal_topic: str = "") -> str:
    if goal_topic:
        topic_part = f"\x1b[38;5;247m[\x1b[0m\x1b[1;38;2;229;138;97m{goal_topic[:15]}\x1b[0m\x1b[38;5;247m]\x1b[0m"
    else:
        topic_part = ""
    return f"  \x1b[1;38;2;229;138;97mohmy\x1b[0m{topic_part}\x1b[38;5;247m[\x1b[0m\x1b[38;5;244m{_short_model_name(model_name)}\x1b[0m\x1b[38;5;247m] > \x1b[0m"


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
