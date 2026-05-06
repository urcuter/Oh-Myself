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
        plan_topics=("强化学习", "毕业论文"),
    )

    completions = list(completer.get_completions(Document("/plan 强"), None))

    assert any(item.text == "/plan 强化学习：" for item in completions)
