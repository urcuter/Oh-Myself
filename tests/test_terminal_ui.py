from __future__ import annotations

from prompt_toolkit.document import Document

from ohmyself.terminal_ui import (
    _SlashCommandCompleter,
    _welcome_divider,
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
