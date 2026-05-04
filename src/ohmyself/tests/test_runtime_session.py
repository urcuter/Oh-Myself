from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from pathlib import Path

from ohmyself.api.client import ApiMessageCompleteEvent
from ohmyself.api.usage import UsageSnapshot
from ohmyself.engine.messages import ConversationMessage, TextBlock
from ohmyself.runtime import build_runtime


class _StaticApiClient:
    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ready")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class RuntimeSessionTests(unittest.TestCase):
    def test_runtime_tracks_and_rotates_session_id(self) -> None:
        async def _build_and_check():
            runtime = await build_runtime(
                cwd=str(Path.cwd()),
                api_client=_StaticApiClient(),
                permission_mode="full_auto",
            )
            self.assertEqual(runtime.session_id, runtime.engine.tool_metadata["session_id"])
            previous_id = runtime.session_id
            runtime.start_new_session()
            self.assertNotEqual(runtime.session_id, previous_id)
            self.assertEqual(runtime.engine.tool_metadata["session_id"], runtime.session_id)

        asyncio.run(_build_and_check())

    def test_runtime_restores_snapshot_messages_and_identity(self) -> None:
        async def _build_and_check():
            runtime = await build_runtime(
                cwd=str(Path.cwd()),
                api_client=_StaticApiClient(),
                permission_mode="full_auto",
            )
            snapshot = {
                "session_id": "restored-001",
                "session_started_at": datetime.fromisoformat("2026-05-04T11:00:00+08:00").isoformat(),
                "model": "restored-model",
                "messages": [
                    ConversationMessage.from_user_text("hello"),
                    ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
                ],
            }
            runtime.restore_session_snapshot(snapshot)
            self.assertEqual(runtime.session_id, "restored-001")
            self.assertEqual(runtime.engine.tool_metadata["session_id"], "restored-001")
            self.assertEqual(len(runtime.engine.messages), 2)
            self.assertEqual(runtime.engine.messages[0].text, "hello")
            self.assertEqual(runtime.engine.model, "restored-model")
            self.assertNotIn("# Layer 2: User Prompt", runtime.engine.system_prompt)
            self.assertIn("# Environment", runtime.engine.system_prompt)

        asyncio.run(_build_and_check())


if __name__ == "__main__":
    unittest.main()
