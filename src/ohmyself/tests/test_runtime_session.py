from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from pathlib import Path

from ohmyself.api.client import ApiMessageCompleteEvent
from ohmyself.api.usage import UsageSnapshot
from ohmyself.cli import _perform_goal_exit, _perform_goal_switch
from ohmyself.engine.messages import ConversationMessage, TextBlock
from ohmyself.runtime import build_runtime
from ohmyself.services.goal import append_goal
from ohmyself.services.goal_agent import GoalAgentContext


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

    def test_goal_switch_updates_runtime_cwd_and_exit_restores(self) -> None:
        async def _build_and_check():
            root = Path.cwd() / ".pytest-temp-runtime-goal-cwd"
            workspace = root / "project"
            home = root / "home"
            linked = workspace / "goal-a"
            linked.mkdir(parents=True, exist_ok=True)

            old_home = os.environ.get("OHMYSELF_HOME")
            os.environ["OHMYSELF_HOME"] = str(home)
            try:
                runtime = await build_runtime(
                    cwd=str(workspace),
                    api_client=_StaticApiClient(),
                    permission_mode="full_auto",
                )
                runtime.goal_context = GoalAgentContext()
                goal = append_goal("Goal A", linked_dir=str(linked))

                class _TranscriptStub:
                    def __init__(self) -> None:
                        self.resets: list[tuple[str, str, str]] = []
                        self.statuses: list[tuple[str, str]] = []

                    def reset_session(self, *, session_id: str, cwd: str, model: str, started_at: datetime) -> None:
                        del started_at
                        self.resets.append((session_id, cwd, model))

                    def record_status(self, category: str, message: str) -> None:
                        self.statuses.append((category, message))

                transcript = _TranscriptStub()
                _perform_goal_switch(runtime, transcript, goal.entry_id)

                self.assertEqual(runtime.base_cwd, str(workspace.resolve()))
                self.assertEqual(runtime.cwd, str(linked.resolve()))
                self.assertEqual(runtime.linked_dir, str(linked.resolve()))
                self.assertEqual(runtime.engine.tool_metadata["linked_dir"], str(linked.resolve()))
                self.assertEqual(transcript.resets[-1], (runtime.session_id, str(linked.resolve()), runtime.current_model()))

                await _perform_goal_exit(runtime, transcript)

                self.assertEqual(runtime.cwd, str(workspace.resolve()))
                self.assertIsNone(runtime.linked_dir)
                self.assertIsNone(runtime.engine.tool_metadata["linked_dir"])
                self.assertEqual(transcript.resets[-1], (runtime.session_id, str(workspace.resolve()), runtime.current_model()))
            finally:
                if old_home is None:
                    os.environ.pop("OHMYSELF_HOME", None)
                else:
                    os.environ["OHMYSELF_HOME"] = old_home

        import os

        asyncio.run(_build_and_check())

    def test_goal_switch_with_missing_linked_dir_stays_in_base_workspace(self) -> None:
        async def _build_and_check():
            root = Path.cwd() / ".pytest-temp-runtime-goal-missing"
            workspace = root / "project"
            home = root / "home"
            missing = workspace / "missing-goal-dir"
            workspace.mkdir(parents=True, exist_ok=True)

            old_home = os.environ.get("OHMYSELF_HOME")
            os.environ["OHMYSELF_HOME"] = str(home)
            try:
                runtime = await build_runtime(
                    cwd=str(workspace),
                    api_client=_StaticApiClient(),
                    permission_mode="full_auto",
                )
                runtime.goal_context = GoalAgentContext()
                goal = append_goal("Goal A", linked_dir=str(missing))

                class _TranscriptStub:
                    def reset_session(self, *, session_id: str, cwd: str, model: str, started_at: datetime) -> None:
                        del session_id, cwd, model, started_at

                    def record_status(self, category: str, message: str) -> None:
                        del category, message

                _perform_goal_switch(runtime, _TranscriptStub(), goal.entry_id)

                self.assertEqual(runtime.cwd, str(workspace.resolve()))
                self.assertIsNone(runtime.linked_dir)
                self.assertIsNone(runtime.engine.tool_metadata["linked_dir"])
            finally:
                if old_home is None:
                    os.environ.pop("OHMYSELF_HOME", None)
                else:
                    os.environ["OHMYSELF_HOME"] = old_home

        import os

        asyncio.run(_build_and_check())


if __name__ == "__main__":
    unittest.main()
