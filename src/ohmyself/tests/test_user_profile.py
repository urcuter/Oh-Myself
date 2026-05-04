from __future__ import annotations

import asyncio
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ohmyself.api.client import ApiMessageCompleteEvent
from ohmyself.api.usage import UsageSnapshot
from ohmyself.cli import _build_transcript_writer, _handle_user_profile_command
from ohmyself.engine.messages import ConversationMessage, TextBlock
from ohmyself.runtime import build_runtime
from ohmyself.services.user_profile import generate_user_profile


class _ProfileApiClient:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.requests = []

    async def stream_message(self, request):
        self.requests.append(request)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self.response_text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class UserProfileTests(unittest.TestCase):
    def test_generate_user_profile_uses_conversation_and_instruction(self) -> None:
        async def _run():
            client = _ProfileApiClient("# Preferences\n- Prefer Chinese answers")
            result = await generate_user_profile(
                api_client=client,
                model="gpt-test",
                max_tokens=256,
                conversation=[
                    ConversationMessage.from_user_text("Please answer in Chinese."),
                    ConversationMessage(role="assistant", content=[TextBlock(text="OK")]),
                ],
                memory_dir=Path.cwd() / "tmp-home-user-profile-a" / "memory",
                extra_instruction="Focus on language preference.",
            )
            self.assertEqual(result, "# Preferences\n- Prefer Chinese answers")
            self.assertEqual(len(client.requests), 1)
            request = client.requests[0]
            self.assertEqual(request.model, "gpt-test")
            self.assertEqual(request.tools, [])
            self.assertIn("Please answer in Chinese.", request.messages[0].text)
            self.assertIn("Focus on language preference.", request.messages[0].text)

        asyncio.run(_run())

    def test_handle_user_profile_command_writes_profile_file(self) -> None:
        original_home = os.environ.get("OHMYSELF_HOME")
        try:
            home = Path.cwd() / "tmp-home-user-profile-b"
            os.environ["OHMYSELF_HOME"] = str(home)

            async def _run():
                client = _ProfileApiClient("# Preferences\n- Keep replies concise")
                runtime = await build_runtime(
                    cwd=str(Path.cwd()),
                    api_client=client,
                    permission_mode="full_auto",
                )
                runtime.engine.load_messages([ConversationMessage.from_user_text("Keep replies concise.")])
                transcript = _build_transcript_writer(runtime)
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    await _handle_user_profile_command(runtime, transcript, "Keep it short and technical.")
                output = buffer.getvalue()
                self.assertIn("Updated user profile", output)
                profile_path = home / "memory" / "user_profile.md"
                self.assertTrue(profile_path.exists())
                self.assertIn("Keep replies concise", profile_path.read_text(encoding="utf-8"))

            asyncio.run(_run())
        finally:
            if original_home is None:
                os.environ.pop("OHMYSELF_HOME", None)
            else:
                os.environ["OHMYSELF_HOME"] = original_home


if __name__ == "__main__":
    unittest.main()
