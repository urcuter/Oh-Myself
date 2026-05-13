from __future__ import annotations

import asyncio  # 异步 I/O 模块
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ohmyself.engine.messages import ConversationMessage
from ohmyself.engine.query import MaxTurnsExceeded, QueryContext, run_query
from ohmyself.engine.stream_events import AssistantTurnComplete, ErrorEvent, ToolExecutionCompleted, ToolExecutionStarted
from ohmyself.permissions import PermissionChecker, PermissionMode
from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult

_DEFAULT_READ_ONLY_TOOLS = ("bash", "read_file", "glob", "grep", "tool_search")
_DEFAULT_CHILD_MAX_TURNS = 6
_MAX_CHILD_MAX_TURNS = 12
_MAX_SUBAGENT_DEPTH = 1
_DEFAULT_CHILD_TIMEOUT_SECONDS = 90.0
_DEFAULT_MAX_CONCURRENT_SUBAGENTS = 2


class DelegateTaskToolInput(BaseModel):
    task: str = Field(description="The bounded task for the delegated subagent.")
    role: str = Field(default="specialist", description="Short role label such as researcher, coder, or reviewer.")
    context: str = Field(default="", description="Extra context or constraints that the child agent should consider.")
    allowed_tools: list[str] = Field(
        default_factory=list, 
        description="Optional subset of tool names exposed to the child agent.",
    )
    read_only: bool = Field(
        default=True,
        description="When true, the child agent runs in read-only mode and cannot use mutating tools.",
    )
    max_turns: int = Field(
        default=_DEFAULT_CHILD_MAX_TURNS,
        ge=1,
        le=_MAX_CHILD_MAX_TURNS,
        description="Maximum internal turns for the child agent.",
    )
    timeout_seconds: float = Field(
        default=_DEFAULT_CHILD_TIMEOUT_SECONDS,
        ge=0.1,
        le=600.0,
        description="Maximum wall-clock time for the child agent run.",
    )


@dataclass(frozen=True)
class _ChildRunResult:
    session_id: str
    final_text: str
    tool_events: list[str]
    is_error: bool
    timed_out: bool = False


class DelegateTaskTool(BaseTool):
    name = "delegate_task"
    description = (
        "Delegate a focused subtask to a child agent. Use for bounded work such as research, code review, "
        "or implementing a small isolated change. Keep the final user-facing response in the main agent."
    )
    input_model = DelegateTaskToolInput

    def is_read_only(self, arguments: DelegateTaskToolInput) -> bool:
        return arguments.read_only

    async def execute(self, arguments: DelegateTaskToolInput, context: ToolExecutionContext) -> ToolResult:
        current_depth = int(context.metadata.get("agent_depth", 0) or 0)
        if current_depth >= _MAX_SUBAGENT_DEPTH:
            return ToolResult(
                output=f"Subagent depth limit reached ({_MAX_SUBAGENT_DEPTH}); delegate this task from the main agent instead.",
                is_error=True,
            )

        api_client = context.metadata.get("api_client")
        permission_checker = context.metadata.get("permission_checker")
        parent_registry = context.metadata.get("tool_registry")
        model = context.metadata.get("model")
        system_prompt = context.metadata.get("system_prompt")
        max_tokens = context.metadata.get("max_tokens")
        permission_prompt = context.metadata.get("permission_prompt")
        if not isinstance(parent_registry, ToolRegistry) or not isinstance(permission_checker, PermissionChecker):
            return ToolResult(output="Subagent runtime metadata is incomplete.", is_error=True)
        if api_client is None or not isinstance(model, str) or not isinstance(system_prompt, str) or not isinstance(max_tokens, int):
            return ToolResult(output="Subagent execution is unavailable in the current runtime.", is_error=True)

        child_registry = _build_child_registry(
            parent_registry,
            allowed_tools=arguments.allowed_tools,
            read_only=arguments.read_only,
        )

        if not child_registry.list_tools():
            return ToolResult(output="No tools are available for the delegated subagent.", is_error=True)

        child_session_id = uuid4().hex[:12] # 生成一个随机的子会话 ID，长度为 12 个十六进制字符
        child_metadata = {
            "session_id": child_session_id,
            "parent_session_id": context.metadata.get("session_id"),
            "tool_registry": child_registry,
            "active_profile": context.metadata.get("active_profile"),
            "active_artifacts": [],
            "last_goal": arguments.task,
            "agent_depth": current_depth + 1,
            "agent_lineage": [*(context.metadata.get("agent_lineage") or []), child_session_id],
            "subagent_role": arguments.role,
            "subagent_runs": [],
        }
        child_context = QueryContext(
            api_client=api_client,
            tool_registry=child_registry,
            permission_checker=_child_permission_checker(permission_checker, force_read_only=arguments.read_only),
            cwd=context.cwd,
            model=model,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            permission_prompt=permission_prompt,
            max_turns=arguments.max_turns,
            tool_metadata=child_metadata,
        )
        child_prompt = _build_child_prompt(
            task=arguments.task,
            role=arguments.role,
            context=arguments.context,
            parent_goal=str(context.metadata.get("last_goal") or "").strip(),
            read_only=arguments.read_only,
        )
        semaphore = _resolve_subagent_semaphore(context.metadata)
        async with semaphore:
            result = await _run_child_query_with_timeout(
                child_context,
                child_prompt,
                timeout_seconds=arguments.timeout_seconds,
            )
        _record_subagent_run(
            context.metadata,
            {
                "session_id": result.session_id,
                "role": arguments.role,
                "task": arguments.task,
                "read_only": arguments.read_only,
                "is_error": result.is_error,
                "timed_out": result.timed_out,
            },
        )
        return ToolResult(
            output=_format_child_result(arguments, result),
            is_error=result.is_error,
            metadata={
                "subagent": {
                    "session_id": result.session_id,
                    "role": arguments.role,
                    "task": arguments.task,
                    "summary": result.final_text,
                    "timed_out": result.timed_out,
                }
            },
        )


def _build_child_registry(parent_registry: ToolRegistry, *, allowed_tools: list[str], read_only: bool) -> ToolRegistry:
    registry = ToolRegistry()
    explicit_subset = bool(allowed_tools) # 是否显式指定了工具子集
    allowed_set = {name for name in allowed_tools if name and name != DelegateTaskTool.name}
    if explicit_subset and not allowed_set:
        return registry
    if read_only and not allowed_set:
        allowed_set = set(_DEFAULT_READ_ONLY_TOOLS)
    for tool in parent_registry.list_tools():
        if tool.name == DelegateTaskTool.name:
            continue
        if allowed_set and tool.name not in allowed_set:
            continue
        registry.register(tool)
    return registry


def _child_permission_checker(parent_checker: PermissionChecker, *, force_read_only: bool) -> PermissionChecker:
    if not force_read_only:
        return parent_checker
    return parent_checker.with_mode(PermissionMode.PLAN)


def _resolve_subagent_semaphore(metadata: dict[str, Any]) -> asyncio.Semaphore:
    semaphore = metadata.get("subagent_semaphore")
    if isinstance(semaphore, asyncio.Semaphore):
        return semaphore
    runtime_metadata = metadata.get("runtime_tool_metadata")
    if isinstance(runtime_metadata, dict):
        runtime_semaphore = runtime_metadata.get("subagent_semaphore")
        if isinstance(runtime_semaphore, asyncio.Semaphore):
            return runtime_semaphore
        runtime_metadata["subagent_semaphore"] = asyncio.Semaphore(_DEFAULT_MAX_CONCURRENT_SUBAGENTS)
        return runtime_metadata["subagent_semaphore"]
    return asyncio.Semaphore(_DEFAULT_MAX_CONCURRENT_SUBAGENTS)


def _build_child_prompt(*, task: str, role: str, context: str, parent_goal: str, read_only: bool) -> str:
    lines = [
        "You are a delegated subagent working for another agent, not the end user.",
        f"Role: {role}",
        f"Task: {task.strip()}",
    ]
    if parent_goal:
        lines.append(f"Parent goal: {parent_goal}")
    if context.strip():
        lines.extend(["Additional context:", context.strip()])
    lines.extend(
        [
            "",
            "Constraints:",
            "- Stay tightly scoped to the delegated task.",
            "- Do not ask the end user questions.",
            "- Return a concise final report with sections: Result, Evidence, Files Changed, Open Questions.",
        ]
    )
    if read_only:
        lines.append("- Read-only mode is active. Do not modify files or run mutating shell commands.")
    return "\n".join(lines)


async def _run_child_query(context: QueryContext, prompt: str) -> _ChildRunResult:
    messages = [ConversationMessage.from_user_text(prompt)]
    final_text = ""
    tool_events: list[str] = []
    is_error = False
    try:
        async for event, _usage in run_query(context, messages):
            if isinstance(event, AssistantTurnComplete) and event.message.text.strip():
                final_text = event.message.text.strip()
            elif isinstance(event, ToolExecutionStarted):
                tool_events.append(f"tool:start {event.tool_name}")
            elif isinstance(event, ToolExecutionCompleted):
                status = "error" if event.is_error else "done"
                tool_events.append(f"tool:{status} {event.tool_name}")
            elif isinstance(event, ErrorEvent):
                final_text = event.message
                is_error = True
    except MaxTurnsExceeded as exc:
        final_text = f"Subagent stopped after reaching max_turns={exc.max_turns}."
        is_error = True
    if not final_text:
        final_text = "(no final report)"
        is_error = True
    return _ChildRunResult(
        session_id=str(context.tool_metadata.get("session_id") if context.tool_metadata else ""),
        final_text=final_text,
        tool_events=tool_events,
        is_error=is_error,
    )


async def _run_child_query_with_timeout(context: QueryContext, prompt: str, *, timeout_seconds: float) -> _ChildRunResult:
    try:
        return await asyncio.wait_for(_run_child_query(context, prompt), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return _ChildRunResult(
            session_id=str(context.tool_metadata.get("session_id") if context.tool_metadata else ""),
            final_text=f"Subagent timed out after {timeout_seconds:.1f} seconds.",
            tool_events=[],
            is_error=True,
            timed_out=True,
        )


def _record_subagent_run(metadata: dict[str, Any], entry: dict[str, Any]) -> None:
    runs = metadata.get("subagent_runs")
    if isinstance(runs, list):
        runs.append(entry)


def _format_child_result(arguments: DelegateTaskToolInput, result: _ChildRunResult) -> str:
    lines = [
        f"Subagent session: {result.session_id}",
        f"Role: {arguments.role}",
        f"Read-only: {arguments.read_only}",
        "",
        result.final_text,
    ]
    if result.tool_events:
        lines.extend(["", "Tool activity:"])
        lines.extend(f"- {item}" for item in result.tool_events)
    return "\n".join(lines).strip()
