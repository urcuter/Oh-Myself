from __future__ import annotations

from prompt_toolkit.document import Document

from ohmyself.terminal_ui import _SlashCommandCompleter, print_error, print_help_panel, print_status, print_success


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


def test_slash_command_completer_suggests_local_commands():
    completer = _SlashCommandCompleter(
        (
            ("/help", "Show local commands"),
            ("/exper add [content]", "Add a life experience"),
        )
    )

    completions = list(completer.get_completions(Document("/"), None))

    assert {item.text for item in completions} == {"/help", "/exper add"}
