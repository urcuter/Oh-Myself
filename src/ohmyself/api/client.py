"""Shared API request/stream protocol types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

from ohmyself.api.usage import UsageSnapshot
from ohmyself.engine.messages import ConversationMessage


@dataclass(frozen=True)  # 定义一个不可变的数据类 ApiMessageRequest，包含模型名称、消息列表、系统提示、最大令牌数和工具列表等字段。
class ApiMessageRequest:
    model: str
    messages: list[ConversationMessage]
    system_prompt: str | None = None
    max_tokens: int = 4096  # 默认输出长度
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ApiTextDeltaEvent:
    text: str


@dataclass(frozen=True)
class ApiMessageCompleteEvent:
    message: ConversationMessage
    usage: UsageSnapshot
    stop_reason: str | None = None


@dataclass(frozen=True)
class ApiRetryEvent:
    message: str
    attempt: int
    max_attempts: int
    delay_seconds: float


ApiStreamEvent = ApiTextDeltaEvent | ApiMessageCompleteEvent | ApiRetryEvent  
# 定义一个联合类型 ApiStreamEvent，可以是文本增量事件、消息完成事件或重试事件中的任意一种，用于表示流式消息的不同事件类型。


class SupportsStreamingMessages(Protocol):  
    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        ...

