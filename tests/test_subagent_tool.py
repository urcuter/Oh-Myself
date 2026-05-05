from __future__ import annotations

import asyncio
from pathlib import Path

from ohmyself.api.client import ApiMessageCompleteEvent
from ohmyself.api.usage import UsageSnapshot
from ohmyself.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from ohmyself.engine.stream_events import SubagentCompleted, SubagentStarted
from ohmyself.permissions import PermissionChecker
from ohmyself.config.settings import PermissionSettings
from ohmyself.runtime import build_runtime
from ohmyself.tools.base import ToolExecutionContext
from ohmyself.tools.subagent_tool import DelegateTaskTool, DelegateTaskToolInput


class _DelegatingApiClient:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_message(self, request):
        self.calls += 1
        messages = request.messages
        if self.calls == 1:
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="toolu_delegate_1",
                            name="delegate_task",
                            input={"task": "Inspect the workspace and report findings.", "role": "researcher"},
                        )
                    ],
                ),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason=None,
            )
            return
        if self.calls == 2:
            assert len(messages) == 1
            assert "delegated subagent" in messages[0].text.lower()
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text="## Result\nThe repository exposes a small local tool registry.")],
                ),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason=None,
            )
            return
        assert self.calls == 3
        assert len(messages) == 3
        assert "Subagent session:" in messages[-1].content[0].content
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="Parent completed.")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


def test_delegate_task_runs_child_agent_and_records_run(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    async def _run():
        runtime = await build_runtime(
            cwd=str(tmp_path),
            api_client=_DelegatingApiClient(),
            permission_mode="full_auto",
        )
        events = []
        async for event in runtime.engine.submit_message("Please inspect the repository."):
            events.append(event)

        assert runtime.engine.messages[-1].text == "Parent completed."
        assert runtime.engine.tool_metadata["subagent_runs"]
        assert runtime.engine.tool_metadata["subagent_semaphore"]
        child_run = runtime.engine.tool_metadata["subagent_runs"][0]
        assert child_run["role"] == "researcher"
        assert child_run["is_error"] is False
        assert child_run["timed_out"] is False
        tool_results = [message for message in runtime.engine.messages if message.role == "user" and message.content]
        assert any("Subagent session:" in block.content for message in tool_results for block in message.content if hasattr(block, "content"))
        assert any(isinstance(event, SubagentStarted) for event in events)
        completed = next(event for event in events if isinstance(event, SubagentCompleted))
        assert completed.role == "researcher"
        assert completed.is_error is False
        assert events

    asyncio.run(_run())


def test_delegate_task_rejects_nested_depth(tmp_path: Path):
    tool = DelegateTaskTool()
    result = asyncio.run(
        tool.execute(
            DelegateTaskToolInput(task="Nested work"),
            ToolExecutionContext(
                cwd=tmp_path,
                metadata={"agent_depth": 1},
            ),
        )
    )
    assert result.is_error is True
    assert "depth limit" in result.output.lower()


class _SlowApiClient:
    async def stream_message(self, request):
        del request
        await asyncio.sleep(0.2)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="too late")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


def test_delegate_task_times_out(tmp_path: Path):
    tool = DelegateTaskTool()

    # Build a real runtime once so the tool receives a valid registry and metadata shape.
    async def _build_and_run():
        runtime = await build_runtime(
            cwd=str(tmp_path),
            api_client=_SlowApiClient(),
            permission_mode="full_auto",
        )
        result = await tool.execute(
            DelegateTaskToolInput(task="Wait", timeout_seconds=0.1),
            ToolExecutionContext(
                cwd=tmp_path,
                metadata={
                    **runtime.engine.tool_metadata,
                    "api_client": runtime.engine.api_client,
                    "permission_checker": PermissionChecker(PermissionSettings(mode="full_auto")),
                    "tool_registry": runtime.engine.tool_metadata["tool_registry"],
                    "model": runtime.engine.model,
                    "system_prompt": runtime.engine.system_prompt,
                    "max_tokens": runtime.engine.max_tokens,
                    "permission_prompt": None,
                    "runtime_tool_metadata": runtime.engine.tool_metadata,
                },
            ),
        )
        return result

    result = asyncio.run(_build_and_run())
    assert result.is_error is True
    assert "timed out" in result.output.lower()
    assert result.metadata["subagent"]["timed_out"] is True
