from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ohmyself.api.usage import UsageSnapshot
from ohmyself.engine.messages import ConversationMessage


@dataclass(frozen=True)
class AssistantTextDelta:
    text: str


@dataclass(frozen=True)
class AssistantTurnComplete:
    message: ConversationMessage
    usage: UsageSnapshot


@dataclass(frozen=True)
class ToolExecutionStarted:
    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionCompleted:
    tool_name: str
    output: str
    is_error: bool = False


@dataclass(frozen=True)
class SubagentStarted:
    tool_use_id: str
    role: str
    task: str
    read_only: bool


@dataclass(frozen=True)
class SubagentCompleted:
    tool_use_id: str
    session_id: str
    role: str
    summary: str
    is_error: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class ErrorEvent:
    message: str
    recoverable: bool = True


@dataclass(frozen=True)
class StatusEvent:
    message: str


StreamEvent = (
    AssistantTextDelta
    | AssistantTurnComplete
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | SubagentStarted
    | SubagentCompleted
    | ErrorEvent
    | StatusEvent
)
