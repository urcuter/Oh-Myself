from __future__ import annotations

import os
import unittest
from pathlib import Path

from ohmyself.prompts.environment import EnvironmentInfo
from ohmyself.prompts.system_prompt import build_system_prompt


def _fake_env() -> EnvironmentInfo:
    return EnvironmentInfo(
        os_name="Windows",
        os_version="10",
        platform_machine="AMD64",
        shell="powershell",
        cwd=str(Path.cwd()),
        date="2026-05-04T16:00:00+08:00",
        python_version="3.13.1",
        python_executable="D:/python/python.exe",
    )


class SystemPromptTests(unittest.TestCase):
    def test_system_prompt_uses_three_layers(self) -> None:
        original_home = os.environ.get("OHMYSELF_HOME")
        try:
            os.environ["OHMYSELF_HOME"] = str(Path.cwd() / "tmp-home-prompt-a")
            prompt = build_system_prompt("Follow user preference.", env=_fake_env(), cwd=str(Path.cwd()))
        finally:
            if original_home is None:
                os.environ.pop("OHMYSELF_HOME", None)
            else:
                os.environ["OHMYSELF_HOME"] = original_home
        self.assertIn("# Layer 1: Base Prompt", prompt)
        self.assertIn("# Layer 2: User Prompt", prompt)
        self.assertIn("Follow user preference.", prompt)
        self.assertIn("# Environment", prompt)
        self.assertTrue((Path.cwd() / "tmp-home-prompt-a" / "memory" / "model_profile.md").exists())
        self.assertLess(prompt.index("# Layer 1: Base Prompt"), prompt.index("# Layer 2: User Prompt"))
        self.assertLess(prompt.index("# Layer 2: User Prompt"), prompt.index("# Environment"))

    def test_system_prompt_loads_user_profile_memory(self) -> None:
        home = Path.cwd() / "tmp-home-prompt-b"
        original_home = os.environ.get("OHMYSELF_HOME")
        try:
            os.environ["OHMYSELF_HOME"] = str(home)
            memory_path = home / "memory" / "user_profile.md"
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text("User prefers concise technical answers.", encoding="utf-8")
            prompt = build_system_prompt(None, env=_fake_env(), cwd=str(Path.cwd()))
        finally:
            if original_home is None:
                os.environ.pop("OHMYSELF_HOME", None)
            else:
                os.environ["OHMYSELF_HOME"] = original_home
        self.assertIn("# Layer 3 Memory: User Profile", prompt)
        self.assertIn("User prefers concise technical answers.", prompt)

    def test_system_prompt_loads_model_profile_memory(self) -> None:
        home = Path.cwd() / "tmp-home-prompt-c"
        original_home = os.environ.get("OHMYSELF_HOME")
        try:
            os.environ["OHMYSELF_HOME"] = str(home)
            memory_path = home / "memory" / "model_profile.md"
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text("Act as a quiet, rigorous coding assistant.", encoding="utf-8")
            prompt = build_system_prompt(None, env=_fake_env(), cwd=str(Path.cwd()))
        finally:
            if original_home is None:
                os.environ.pop("OHMYSELF_HOME", None)
            else:
                os.environ["OHMYSELF_HOME"] = original_home
        self.assertIn("# Layer 3: Model Profile", prompt)
        self.assertIn("Act as a quiet, rigorous coding assistant.", prompt)


if __name__ == "__main__":
    unittest.main()
