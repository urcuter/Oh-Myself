from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable
from uuid import uuid4

from ohmyself.api.client import ApiMessageCompleteEvent, ApiMessageRequest, ApiRetryEvent, ApiTextDeltaEvent, SupportsStreamingMessages
from ohmyself.api.usage import UsageSnapshot
from ohmyself.config.paths import get_data_dir
from ohmyself.engine.messages import ConversationMessage, ToolResultBlock
from ohmyself.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    SubagentCompleted,
    SubagentStarted,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from ohmyself.permissions.checker import PermissionChecker, PermissionDecision
from ohmyself.services.tool_outputs import tool_output_inline_chars, tool_output_preview_chars
from ohmyself.tools.base import ToolExecutionContext, ToolRegistry

PermissionPrompt = Callable[[str, str], Awaitable[bool]]


class MaxTurnsExceeded(RuntimeError):
    def __init__(self, max_turns: int) -> None:
        super().__init__(f"Exceeded maximum turn limit ({max_turns})")
        self.max_turns = max_turns


@dataclass
class QueryContext:
    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    permission_checker: PermissionChecker
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    permission_prompt: PermissionPrompt | None = None
    max_turns: int | None = 200
    tool_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class _ExecutedToolResult:
    block: ToolResultBlock
    metadata: dict[str, object]


def remember_user_goal(tool_metadata: dict[str, object] | None, prompt: str) -> None:
    if tool_metadata is None:
        return
    tool_metadata["last_goal"] = " ".join(prompt.split())[:240]


def _should_auto_allow_write_file(
    tool_metadata: dict[str, object] | None,
    *,
    tool_name: str,
    decision_reason: str,
    requires_confirmation: bool,
) -> bool:
    if tool_name != "write_file" or not isinstance(tool_metadata, dict):
        return False
    if not bool(tool_metadata.get("auto_allow_write_file_for_plan")):
        return False
    return requires_confirmation or decision_reason == "Plan mode blocks mutating tools."


def _safe_tool_artifact_name(tool_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name.strip())
    return (normalized or "tool")[:80]


def _offload_tool_output_if_needed(*, tool_name: str, tool_use_id: str, output: str) -> tuple[str, Path | None]:
    inline_limit = tool_output_inline_chars()
    if len(output) <= inline_limit:
        return output, None
    artifact_dir = get_data_dir() / "tool_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{_safe_tool_artifact_name(tool_name)}-{uuid4().hex[:12]}.txt"
    artifact_path.write_text(output, encoding="utf-8", errors="replace")
    preview = output[:tool_output_preview_chars()]
    omitted = max(0, len(output) - len(preview))
    inline = (
        "[Tool output truncated]\n"
        f"Tool: {tool_name}\n"
        f"Tool use id: {tool_use_id}\n"
        f"Original size: {len(output)} chars\n"
        f"Full output saved to: {artifact_path}\n"
        f"Inline preview: first {len(preview)} chars"
    )
    if omitted:
        inline += f" ({omitted} chars omitted)"
    if preview:
        inline += f"\n\nPreview:\n{preview}"
    return inline, artifact_path


async def run_query(context: QueryContext, messages: list[ConversationMessage]) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    turn_count = 0
    while context.max_turns is None or turn_count < context.max_turns:
        turn_count += 1
        final_message: ConversationMessage | None = None
        usage = UsageSnapshot()
        try:
            async for event in context.api_client.stream_message(
                ApiMessageRequest(
                    model=context.model,
                    messages=messages,
                    system_prompt=context.system_prompt,
                    max_tokens=context.max_tokens,
                    tools=context.tool_registry.to_api_schema(),
                )
            ):
                if isinstance(event, ApiTextDeltaEvent):
                    yield AssistantTextDelta(text=event.text), None
                elif isinstance(event, ApiRetryEvent):
                    yield StatusEvent(message=f"Request failed; retrying in {event.delay_seconds:.1f}s: {event.message}"), None
                elif isinstance(event, ApiMessageCompleteEvent):
                    final_message = event.message
                    usage = event.usage
        except Exception as exc:
            yield ErrorEvent(message=f"API error: {exc}"), None
            return
        if final_message is None:
            yield ErrorEvent(message="Model stream finished without a final message"), None
            return
        if final_message.role == "assistant" and final_message.is_effectively_empty():
            yield ErrorEvent(message="Model returned an empty assistant message."), usage
            return
        messages.append(final_message)
        yield AssistantTurnComplete(message=final_message, usage=usage), usage
        if not final_message.tool_uses:
            return
        tool_calls = final_message.tool_uses
        if len(tool_calls) == 1:
            tc = tool_calls[0]
            yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None
            if tc.name == "delegate_task":
                yield _subagent_started_event(tc.id, tc.input), None
            result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
            yield ToolExecutionCompleted(tool_name=tc.name, output=result.block.content, is_error=result.block.is_error), None
            if tc.name == "delegate_task":
                yield _subagent_completed_event(tc.id, tc.input, result), None
            tool_results = [result.block]
        else:
            for tc in tool_calls:
                yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None
                if tc.name == "delegate_task":
                    yield _subagent_started_event(tc.id, tc.input), None
            raw_results = await asyncio.gather(
                *[_execute_tool_call(context, tc.name, tc.id, tc.input) for tc in tool_calls],
                return_exceptions=True,
            )
            tool_results = []
            for tc, result in zip(tool_calls, raw_results):
                if isinstance(result, BaseException):
                    result = _ExecutedToolResult(
                        block=ToolResultBlock(tool_use_id=tc.id, content=f"Tool {tc.name} failed: {type(result).__name__}: {result}", is_error=True),
                        metadata={},
                    )
                tool_results.append(result.block)
            for tc, result in zip(tool_calls, tool_results):
                yield ToolExecutionCompleted(tool_name=tc.name, output=result.content, is_error=result.is_error), None
            for tc, result in zip(tool_calls, raw_results):
                if isinstance(result, _ExecutedToolResult) and tc.name == "delegate_task":
                    yield _subagent_completed_event(tc.id, tc.input, result), None
        messages.append(ConversationMessage(role="user", content=tool_results))
    if context.max_turns is not None:
        raise MaxTurnsExceeded(context.max_turns)
    raise RuntimeError("Query loop exited unexpectedly")


async def _execute_tool_call(context: QueryContext, tool_name: str, tool_use_id: str, tool_input: dict[str, object]) -> _ExecutedToolResult:
    tool = context.tool_registry.get(tool_name)
    if tool is None:
        return _ExecutedToolResult(
            block=ToolResultBlock(tool_use_id=tool_use_id, content=f"Unknown tool: {tool_name}", is_error=True),
            metadata={},
        )
    try:
        parsed_input = tool.input_model.model_validate(tool_input)
    except Exception as exc:
        return _ExecutedToolResult(
            block=ToolResultBlock(tool_use_id=tool_use_id, content=f"Invalid input for {tool_name}: {exc}", is_error=True),
            metadata={},
        )
    file_path = _resolve_permission_file_path(context.cwd, tool_input, parsed_input)
    command = _extract_permission_command(tool_input, parsed_input)
    decision = context.permission_checker.evaluate(
        tool_name,
        is_read_only=tool.is_read_only(parsed_input),
        file_path=file_path,
        command=command,
    )
    if not decision.allowed and _should_auto_allow_write_file(
        context.tool_metadata,
        tool_name=tool_name,
        decision_reason=decision.reason,
        requires_confirmation=decision.requires_confirmation,
    ):
        decision = PermissionDecision(True, reason="write_file auto-allowed for /plan")
    if not decision.allowed:
        if decision.requires_confirmation and context.permission_prompt is not None:
            if not await context.permission_prompt(tool_name, decision.reason):
                return _ExecutedToolResult(
                    block=ToolResultBlock(tool_use_id=tool_use_id, content=decision.reason or f"Permission denied for {tool_name}", is_error=True),
                    metadata={},
                )
        else:
            return _ExecutedToolResult(
                block=ToolResultBlock(tool_use_id=tool_use_id, content=decision.reason or f"Permission denied for {tool_name}", is_error=True),
                metadata={},
            )
    result = await tool.execute(
        parsed_input,
        ToolExecutionContext(
            cwd=context.cwd,
            metadata={
                "api_client": context.api_client,
                "tool_registry": context.tool_registry,
                "permission_checker": context.permission_checker,
                "model": context.model,
                "system_prompt": context.system_prompt,
                "max_tokens": context.max_tokens,
                "permission_prompt": context.permission_prompt,
                "runtime_tool_metadata": context.tool_metadata,
                **(context.tool_metadata or {}),
            },
        ),
    )
    inline_output, artifact_path = _offload_tool_output_if_needed(tool_name=tool_name, tool_use_id=tool_use_id, output=result.output)
    if artifact_path is not None and context.tool_metadata is not None:
        artifacts = context.tool_metadata.setdefault("active_artifacts", [])
        if isinstance(artifacts, list):
            artifacts.append(str(artifact_path))
    return _ExecutedToolResult(
        block=ToolResultBlock(tool_use_id=tool_use_id, content=inline_output, is_error=result.is_error),
        metadata=result.metadata,
    )


def _subagent_started_event(tool_use_id: str, tool_input: dict[str, object]) -> SubagentStarted:
    role = str(tool_input.get("role") or "specialist")
    task = str(tool_input.get("task") or "").strip()
    read_only = bool(tool_input.get("read_only", True))
    return SubagentStarted(tool_use_id=tool_use_id, role=role, task=task, read_only=read_only)


def _subagent_completed_event(tool_use_id: str, tool_input: dict[str, object], result: _ExecutedToolResult) -> SubagentCompleted:
    subagent = result.metadata.get("subagent") if isinstance(result.metadata, dict) else None
    if isinstance(subagent, dict):
        session_id = str(subagent.get("session_id") or "")
        role = str(subagent.get("role") or tool_input.get("role") or "specialist")
        summary = str(subagent.get("summary") or result.block.content).strip()
        timed_out = bool(subagent.get("timed_out", False))
    else:
        session_id = ""
        role = str(tool_input.get("role") or "specialist")
        summary = result.block.content.strip()
        timed_out = False
    return SubagentCompleted(
        tool_use_id=tool_use_id,
        session_id=session_id,
        role=role,
        summary=summary,
        is_error=result.block.is_error,
        timed_out=timed_out,
    )


def _resolve_permission_file_path(cwd: Path, raw_input: dict[str, object], parsed_input: object) -> str | None:
    for key in ("file_path", "path", "root"):
        value = raw_input.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = cwd / path
            return str(path.resolve())
    for attr in ("file_path", "path", "root"):
        value = getattr(parsed_input, attr, None)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = cwd / path
            return str(path.resolve())
    return None


def _extract_permission_command(raw_input: dict[str, object], parsed_input: object) -> str | None:
    value = raw_input.get("command")
    if isinstance(value, str) and value.strip():
        return value
    value = getattr(parsed_input, "command", None)
    if isinstance(value, str) and value.strip():
        return value
    return None
