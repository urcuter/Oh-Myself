"""Anthropic-compatible streaming client."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from anthropic import APIError, APIStatusError, AsyncAnthropic

from ohmyself.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiRetryEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
)
from ohmyself.api.errors import AuthenticationFailure, OhMyApiError, RateLimitFailure, RequestFailure
from ohmyself.api.usage import UsageSnapshot
from ohmyself.engine.messages import assistant_message_from_api

log = logging.getLogger(__name__)  # 获取当前模块的日志记录器

MAX_RETRIES = 3 # 定义最大重试次数
BASE_DELAY = 1.0 # 定义基础延迟时间（秒），用于指数退避算法
MAX_DELAY = 30.0 # 定义最大延迟时间（秒），用于限制指数退避算法的最大等待时间
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}  # 定义可重试的HTTP状态码集合，包括429（请求过多）、500（服务器错误）、502（错误网关）、503（服务不可用）和529（过载）等状态码


"""
指数退避（Exponential Backoff） 是一种重试策略，每次重试的等待时间按指数增长。
原理：
第1次重试: delay = BASE_DELAY × 2^0 = 1秒
第2次重试: delay = BASE_DELAY × 2^1 = 2秒
第3次重试: delay = BASE_DELAY × 2^2 = 4秒
第4次重试: delay = BASE_DELAY × 2^3 = 8秒
...
本项目代码 (anthropic_client.py:69)：
delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
# attempt=0 → 1秒
# attempt=1 → 2秒
# attempt=2 → 4秒
# attempt=3 → 8秒 (但上限 MAX_DELAY=30秒)
目的：
1. 避免频繁重试加重服务器负担
2. 给服务器恢复时间
3. 降低多客户端同时重试的冲突概率
常见场景： HTTP 429（限流）、服务器错误、网络超时等临时性故障。
"""

def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, APIError):
        return True
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _translate_api_error(exc: APIError) -> OhMyApiError:
    name = exc.__class__.__name__
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return AuthenticationFailure(str(exc))
    if name == "RateLimitError":
        return RateLimitFailure(str(exc))
    return RequestFailure(str(exc))


class AnthropicApiClient:
    def __init__(self, api_key: str, *, base_url: str | None = None) -> None: # * 表示后续参数必须以关键字参数形式传递
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        last_error: Exception | None = None  # Exception: 定义一个变量 last_error，用于存储最后一次发生的异常，初始值为 None。
        for attempt in range(MAX_RETRIES + 1):
            try:
                async for event in self._stream_once(request):
                    yield event
                return
            except OhMyApiError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not _is_retryable(exc):
                    if isinstance(exc, APIError):
                        raise _translate_api_error(exc) from exc
                    raise RequestFailure(str(exc)) from exc
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                yield ApiRetryEvent(
                    message=str(exc),
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
        if last_error is not None:
            raise RequestFailure(str(last_error)) from last_error

    async def _stream_once(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        params: dict[str, object] = {
            "model": request.model,
            "messages": [message.to_api_param() for message in request.messages],
            "max_tokens": request.max_tokens,
        }
        if request.system_prompt:
            params["system"] = request.system_prompt
        if request.tools:
            params["tools"] = request.tools
        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if getattr(event, "type", None) != "content_block_delta":  # 只处理文本增量事件，忽略其他类型的事件（如工具调用、函数调用等）
                        continue
                    delta = getattr(event, "delta", None)  # 从事件中获取增量内容，可能包含文本、工具调用等信息
                    if getattr(delta, "type", None) != "text_delta":  # 只处理文本增量，忽略其他类型的增量（如工具调用增量等）
                        continue
                    text = getattr(delta, "text", "")  # 从文本增量中提取文本内容，如果没有则默认为空字符串
                    if text:
                        yield ApiTextDeltaEvent(text=text)
                final_message = await stream.get_final_message()
        except APIError as exc:
            if isinstance(exc, APIStatusError) and exc.status_code in RETRYABLE_STATUS_CODES:
                raise
            raise _translate_api_error(exc) from exc
        usage = getattr(final_message, "usage", None)
        yield ApiMessageCompleteEvent(
            message=assistant_message_from_api(final_message),
            usage=UsageSnapshot(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            ),
            stop_reason=getattr(final_message, "stop_reason", None),
        )



"""
异步（async） = 非阻塞执行，等待时可以切换做其他事。
同步 vs 异步对比：
# 同步：阻塞等待
response = requests.get(url)  # 卡在这里，什么都不能做
print(response.text)
# 异步：非阻塞
response = await client.get(url)  # 等待期间可以切换处理其他请求
print(response.text)
---
结合 stream_message 代码解释：
async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
    for attempt in range(MAX_RETRIES + 1):
        try:
            async for event in self._stream_once(request):  # ← 异步迭代
                yield event  # ← 每收到一个事件就 yield 出去
            return
        except Exception as exc:
            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            await asyncio.sleep(delay)  # ← 异步等待，不阻塞整个程序
具体执行过程：
时间线：
─────────────────────────────────────────────────────────►
调用 stream_message()
    │
    ├─→ 发起 HTTP 请求到 OpenAI API
    │   │（网络传输中... 程序可以处理其他请求）
    │   │
    │   ├─→ 收到 chunk 1 → yield ApiTextDeltaEvent("你")
    │   ├─→ 收到 chunk 2 → yield ApiTextDeltaEvent("好")
    │   ├─→ 收到 chunk 3 → yield ApiTextDeltaEvent("！")
    │   └─→ 流结束 → yield ApiMessageCompleteEvent
    │
    └─→ 函数结束
关键点：
1. async def - 声明这是异步函数
2. await - 等待异步操作完成，期间可切换执行其他任务
3. async for - 异步迭代，每次 yield 后控制权返回给调用者
4. yield - 逐步返回数据，流式输出
实际效果：
# 调用方
async for event in client.stream_message(request):
    print(event)  # 收到一个事件就打印一个，不用等全部完成
比喻：
同步	异步
打电话等对方说完才能说下一句	发微信，等回复时可以和别人聊天
排队买票，必须等到你	取号，等待时可以做别的事
适合异步的场景： 网络请求、文件读写、数据库查询等 I/O 操作。

简单说：异步让用户能"看到 AI 在思考和输出"，而不是对着空白屏幕等待。
实现流式输出 +  重试等待时不阻塞
"""