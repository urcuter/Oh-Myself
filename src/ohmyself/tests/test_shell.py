from __future__ import annotations

import unittest
from unittest.mock import patch

from ohmyself.tools.bash_tool import BashTool, BashToolInput
from ohmyself.utils.shell import resolve_shell_command


class ResolveShellCommandTests(unittest.TestCase):
    def test_windows_prefers_powershell_when_bash_probe_fails(self) -> None:
        with (
            patch("ohmyself.utils.shell.os.name", "nt"),
            patch(
                "ohmyself.utils.shell.shutil.which",
                side_effect=lambda name: {
                    "pwsh": None,
                    "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                    "cmd.exe": r"C:\Windows\System32\cmd.exe",
                    "bash": r"C:\Windows\system32\bash.exe",
                }.get(name),
            ),
            patch(
                "ohmyself.utils.shell._shell_is_available",
                side_effect=lambda executable, *args: executable.endswith("powershell.exe"),
            ),
        ):
            argv = resolve_shell_command("python -V")
        self.assertEqual(
            argv,
            [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "python -V",
            ],
        )

    def test_windows_falls_back_to_cmd_when_only_cmd_probe_succeeds(self) -> None:
        with (
            patch("ohmyself.utils.shell.os.name", "nt"),
            patch(
                "ohmyself.utils.shell.shutil.which",
                side_effect=lambda name: {
                    "pwsh": None,
                    "powershell": None,
                    "cmd.exe": r"C:\Windows\System32\cmd.exe",
                    "bash": r"C:\Windows\system32\bash.exe",
                }.get(name),
            ),
            patch(
                "ohmyself.utils.shell._shell_is_available",
                side_effect=lambda executable, *args: executable.endswith("cmd.exe"),
            ),
        ):
            argv = resolve_shell_command("python -V")
        self.assertEqual(
            argv,
            [
                r"C:\Windows\System32\cmd.exe",
                "/d",
                "/s",
                "/c",
                "python -V",
            ],
        )


class BashToolReadOnlyTests(unittest.TestCase):
    def test_read_only_for_common_inspection_commands(self) -> None:
        tool = BashTool()
        self.assertTrue(tool.is_read_only(BashToolInput(command="git status")))
        self.assertTrue(tool.is_read_only(BashToolInput(command="python --version")))
        self.assertTrue(tool.is_read_only(BashToolInput(command='python -c "print(10 ** 2)"')))

    def test_not_read_only_for_commands_with_shell_operators(self) -> None:
        tool = BashTool()
        self.assertFalse(tool.is_read_only(BashToolInput(command="echo hi > out.txt")))

    def test_not_read_only_for_python_inline_code_with_side_effect_risk(self) -> None:
        tool = BashTool()
        self.assertFalse(tool.is_read_only(BashToolInput(command='python -c "open(\'out.txt\', \'w\').write(\'x\')"')))


if __name__ == "__main__":
    unittest.main()
