"""OpenAI-compatible streaming client."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx

from openai import AsyncOpenAI

from ohmyself.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiRetryEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
)
from ohmyself.api.errors import AuthenticationFailure, OhMyApiError, RateLimitFailure, RequestFailure
from ohmyself.api.usage import UsageSnapshot
from ohmyself.engine.messages import ConversationMessage, ContentBlock, ImageBlock, TextBlock, ToolResultBlock, ToolUseBlock

MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_DELAY = 30.0
_MAX_COMPLETION_TOKEN_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_OPEN_TAG = "<think>"


def _token_limit_param_for_model(model: str, max_tokens: int) -> dict[str, int]:
    normalized = model.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    if normalized.startswith(_MAX_COMPLETION_TOKEN_MODEL_PREFIXES):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def _convert_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        }
        for tool in tools
    ]


def _convert_user_content_to_openai(blocks: list[ContentBlock]) -> str | list[dict[str, Any]]:
    has_image = any(isinstance(block, ImageBlock) for block in blocks)
    if not has_image:
        return "".join(block.text for block in blocks if isinstance(block, TextBlock))
    content: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextBlock) and block.text:
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.media_type};base64,{block.data}"},
                }
            )
    return content


def _convert_assistant_message(msg: ConversationMessage) -> dict[str, Any]:
    text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
    tool_uses = [b for b in msg.content if isinstance(b, ToolUseBlock)]
    openai_msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    reasoning = getattr(msg, "_reasoning", None)
    if reasoning:
        openai_msg["reasoning_content"] = reasoning
    elif tool_uses:
        openai_msg["reasoning_content"] = ""
    if tool_uses:
        openai_msg["tool_calls"] = [
            {
                "id": tu.id,
                "type": "function",
                "function": {
                    "name": tu.name,
                    "arguments": json.dumps(tu.input),
                },
            }
            for tu in tool_uses
        ]
    return openai_msg


def _convert_messages_to_openai(messages: list[ConversationMessage], system_prompt: str | None) -> list[dict[str, Any]]:
    openai_messages: list[dict[str, Any]] = []
    if system_prompt:
        openai_messages.append({"role": "system", "content": system_prompt})
    for msg in messages:
        if msg.role == "assistant":
            openai_messages.append(_convert_assistant_message(msg))
            continue
        tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        user_blocks = [b for b in msg.content if isinstance(b, (TextBlock, ImageBlock))]
        for tr in tool_results:
            openai_messages.append(
                {"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content}
            )
        if user_blocks:
            content = _convert_user_content_to_openai(user_blocks)
            if isinstance(content, str):
                openai_messages.append({"role": "user", "content": content})
            elif content:
                openai_messages.append({"role": "user", "content": content})
    return openai_messages


def _normalize_openai_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parts = urlsplit(base_url.strip())
    if not parts.scheme or not parts.netloc:
        return base_url.rstrip("/")
    path = parts.path.rstrip("/") or "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _strip_think_blocks(buf: str) -> tuple[str, str]:
    cleaned = _THINK_RE.sub("", buf)
    open_idx = cleaned.find(_THINK_OPEN_TAG)
    if open_idx != -1:
        return cleaned[:open_idx], cleaned[open_idx:]
    max_prefix = min(len(cleaned), len(_THINK_OPEN_TAG) - 1)
    for prefix_len in range(max_prefix, 0, -1):
        if _THINK_OPEN_TAG.startswith(cleaned[-prefix_len:]):
            return cleaned[:-prefix_len], cleaned[-prefix_len:]
    return cleaned, ""


class OpenAICompatibleClient:
    def __init__(self, api_key: str, *, base_url: str | None = None, timeout: float | None = None) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        normalized_base_url = _normalize_openai_base_url(base_url)
        if normalized_base_url:
            kwargs["base_url"] = normalized_base_url
        # For streaming, read timeout must be None (no limit between chunks).
        # connect/write/pool keep the caller-supplied value (default 30 s).
        connect_timeout = timeout if timeout is not None else 30.0
        kwargs["timeout"] = httpx.Timeout(connect_timeout, read=None)
        self._client = AsyncOpenAI(**kwargs)

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                async for event in self._stream_once(request):
                    yield event
                return
            except OhMyApiError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not self._is_retryable(exc):
                    raise self._translate_error(exc) from exc
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                yield ApiRetryEvent(
                    message=str(exc),
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
        if last_error is not None:
            raise self._translate_error(last_error) from last_error

    async def _stream_once(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        params: dict[str, Any] = {
            "model": request.model,
            "messages": _convert_messages_to_openai(request.messages, request.system_prompt),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        params.update(_token_limit_param_for_model(request.model, request.max_tokens))
        if request.tools:
            params["tools"] = _convert_tools_to_openai(request.tools)
            params.pop("stream_options", None)

        collected_content = ""
        collected_reasoning = ""
        collected_tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage_data: dict[str, int] = {}
        think_buffer = ""

        stream = await self._client.chat.completions.create(**params)
        async for chunk in stream:
            if not chunk.choices:
                if chunk.usage:
                    usage_data = {
                        "input_tokens": chunk.usage.prompt_tokens or 0,
                        "output_tokens": chunk.usage.completion_tokens or 0,
                    }
                continue
            delta = chunk.choices[0].delta
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
            reasoning_piece = getattr(delta, "reasoning_content", None) or ""
            if reasoning_piece:
                collected_reasoning += reasoning_piece
            if delta.content:
                think_buffer += delta.content
                visible, think_buffer = _strip_think_blocks(think_buffer)
                if visible:
                    collected_content += visible
                    yield ApiTextDeltaEvent(text=visible)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    entry = collected_tool_calls.setdefault(
                        idx,
                        {"id": tc_delta.id or "", "name": "", "arguments": ""},
                    )
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments
            if chunk.usage:
                usage_data = {
                    "input_tokens": chunk.usage.prompt_tokens or 0,
                    "output_tokens": chunk.usage.completion_tokens or 0,
                }

        # Flush any text left in think_buffer that never matched a <think> tag.
        if think_buffer:
            collected_content += think_buffer
            yield ApiTextDeltaEvent(text=think_buffer)

        content: list[ContentBlock] = []
        if collected_content:
            content.append(TextBlock(text=collected_content))
        for index in sorted(collected_tool_calls):
            tool_call = collected_tool_calls[index]
            if not tool_call["name"]:
                continue
            try:
                args = json.loads(tool_call["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}
            content.append(
                ToolUseBlock(
                    id=tool_call["id"],
                    name=tool_call["name"],
                    input=args,
                )
            )
        final_message = ConversationMessage(role="assistant", content=content)
        if collected_reasoning:
            final_message._reasoning = collected_reasoning  # type: ignore[attr-defined]
        yield ApiMessageCompleteEvent(
            message=final_message,
            usage=UsageSnapshot(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
            ),
            stop_reason=finish_reason,
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status and status in {429, 500, 502, 503}:
            return True
        return isinstance(exc, (ConnectionError, TimeoutError, OSError))

    @staticmethod
    def _translate_error(exc: Exception) -> OhMyApiError:
        status = getattr(exc, "status_code", None)
        message = str(exc)
        if status in {401, 403}:
            return AuthenticationFailure(message)
        if status == 429:
            return RateLimitFailure(message)
        return RequestFailure(message)

