from __future__ import annotations

from prompt_toolkit.document import Document

from ohmyself.terminal_ui import (
    _SlashCommandCompleter,
    _welcome_divider,
    format_assistant_chunk,
    print_context_snapshot,
    print_error,
    print_help_panel,
    print_status,
    print_success,
    supports_live_markdown,
)


def test_local_notices_have_blank_lines(capsys):
    print_status("status message")
    print_error("error message")
    print_success("success message")

    output = capsys.readouterr().out

    assert "\n      [status] status message\n\n" in output
    assert "\n      [error] error message\n\n" in output
    assert "\n      [done] success message\n\n" in output


def test_help_panel_lists_slash_command_suggestions(capsys):
    print_help_panel()

    output = capsys.readouterr().out

    assert "/" in output
    assert "/help" in output
    assert "/exper add" in output
    assert "/goal [topic]" in output


def test_context_snapshot_prints_status_and_panel(capsys):
    print_context_snapshot("Added goal GOAL-1", title="Goals", markdown="# Goals\n\n- `GOAL-1` 0%")

    output = capsys.readouterr().out

    assert "Added goal GOAL-1" in output
    assert "Goals" in output
    assert "GOAL-1" in output


def test_welcome_divider_uses_ascii_hyphen():
    divider = _welcome_divider(80)

    assert divider.startswith("  ")
    assert set(divider[2:]) == {"-"}
    assert len(divider) == 67


def test_slash_command_completer_suggests_local_commands():
    completer = _SlashCommandCompleter(
        (
            ("/help", "Show local commands"),
            ("/exper add [content]", "Add a life experience"),
        )
    )

    completions = list(completer.get_completions(Document("/"), None))

    assert {item.text for item in completions} == {"/help", "/exper add"}


def test_slash_command_completer_suggests_plan_topics():
    completer = _SlashCommandCompleter(
        (("/plan [content]", "Show or add plan"),),
        plan_topics=("Reinforcement Learning", "Thesis"),
    )

    completions = list(completer.get_completions(Document("/plan R"), None))

    assert any(item.text == "/plan Reinforcement Learning：" for item in completions)


def test_supports_live_markdown_disabled_on_legacy_windows(monkeypatch):
    import ohmyself.terminal_ui as terminal_ui

    class _FakeConsole:
        is_terminal = True
        is_dumb_terminal = False
        legacy_windows = True

    class _FakeStdout:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(terminal_ui, "_CONSOLE", _FakeConsole())
    monkeypatch.setattr(terminal_ui.sys, "stdout", _FakeStdout())

    assert supports_live_markdown() is False


def test_supports_live_markdown_enabled_for_regular_tty(monkeypatch):
    import ohmyself.terminal_ui as terminal_ui

    class _FakeConsole:
        is_terminal = True
        is_dumb_terminal = False
        legacy_windows = False

    class _FakeStdout:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(terminal_ui, "_CONSOLE", _FakeConsole())
    monkeypatch.setattr(terminal_ui.sys, "stdout", _FakeStdout())

    assert supports_live_markdown() is True


class _FakeWideConsole:
    class size:
        width = 120
    legacy_windows = True


class _FakeNarrowConsole:
    class size:
        width = 50
    legacy_windows = True


class _FakeNullWidthConsole:
    class size:
        width = None
    legacy_windows = True


def test_format_assistant_chunk_short_line_no_wrapping(monkeypatch):
    """A line shorter than the terminal width is passed through unchanged."""
    monkeypatch.setattr("ohmyself.terminal_ui._CONSOLE", _FakeWideConsole())
    result, line_start, first = format_assistant_chunk(
        "hello world\n", line_start=True, first_line=True,
    )
    assert result == "    hello world\n"
    assert line_start is True
    assert first is False


def test_format_assistant_chunk_wraps_long_line(monkeypatch):
    """A long complete line is word-wrapped to fit the terminal."""
    monkeypatch.setattr("ohmyself.terminal_ui._CONSOLE", _FakeNarrowConsole())
    text = "The quick brown fox jumps over the lazy dog repeatedly.\n"
    result, line_start, first = format_assistant_chunk(
        text, line_start=True, first_line=True,
    )
    lines = result.splitlines()
    for line in lines:
        assert len(line) <= 50, f"line too long: {line!r}"
    assert "lazy" in result


def test_format_assistant_chunk_partial_line_not_wrapped(monkeypatch):
    """A partial line (no trailing \\n) is NOT wrapped, only indented."""
    monkeypatch.setattr("ohmyself.terminal_ui._CONSOLE", _FakeNarrowConsole())
    text = "A" * 120
    result, line_start, first = format_assistant_chunk(
        text, line_start=True, first_line=True,
    )
    assert result.startswith("    ")
    assert text in result
    assert line_start is False
    assert first is True


def test_format_assistant_chunk_multiple_lines_mixed(monkeypatch):
    """Mix of short, long, and partial lines in one chunk."""
    monkeypatch.setattr("ohmyself.terminal_ui._CONSOLE", _FakeNarrowConsole())
    text = "short\n" + ("word " * 20) + "\npartial"
    result, line_start, first = format_assistant_chunk(
        text, line_start=True, first_line=True,
    )
    lines = result.splitlines()
    assert lines[0] == "    short"
    for line in lines[1:-1]:  # all complete wrapped lines; last is partial
        assert len(line) <= 50, f"line too long: {line!r}"
    assert result.endswith("partial")
    assert line_start is False


def test_format_assistant_chunk_continuation_no_indent(monkeypatch):
    """When line_start=False, no extra indent is prepended."""
    monkeypatch.setattr("ohmyself.terminal_ui._CONSOLE", _FakeWideConsole())
    result, _, _ = format_assistant_chunk(
        " continued\n", line_start=False, first_line=False,
    )
    assert result == " continued\n"


def test_format_assistant_chunk_null_width_safe(monkeypatch):
    """Null console width falls back to _MIN_CONSOLE_WIDTH."""
    monkeypatch.setattr("ohmyself.terminal_ui._CONSOLE", _FakeNullWidthConsole())
    result, _, _ = format_assistant_chunk(
        "hello world\n", line_start=True, first_line=True,
    )
    assert result == "    hello world\n"


def test_format_assistant_chunk_empty_text_noop():
    """Empty input returns empty string and passes through state."""
    result, line_start, first = format_assistant_chunk(
        "", line_start=True, first_line=True,
    )
    assert result == ""
    assert line_start is True
    assert first is True
