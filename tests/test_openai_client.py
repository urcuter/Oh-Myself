from __future__ import annotations

import asyncio
from types import MethodType

import pytest

from ohmyself.api.client import ApiMessageCompleteEvent, ApiMessageRequest, ApiRetryEvent, ApiTextDeltaEvent
from ohmyself.api.errors import RequestFailure
from ohmyself.api.openai_client import OpenAICompatibleClient, _normalize_visible_delta
from ohmyself.api.usage import UsageSnapshot
from ohmyself.engine.messages import ConversationMessage, TextBlock


def test_normalize_visible_delta_handles_cumulative_chunks():
    collected = ""
    raw_chunks = [
        "DQN",
        "DQN core idea",
        "DQN core idea bellman target",
    ]

    visible_chunks = []
    for chunk in raw_chunks:
        visible = _normalize_visible_delta(chunk, collected)
        visible_chunks.append(visible)
        collected += visible

    assert visible_chunks == [
        "DQN",
        " core idea",
        " bellman target",
    ]
    assert collected == raw_chunks[-1]


def test_normalize_visible_delta_preserves_incremental_chunks():
    collected = "DQN"

    visible = _normalize_visible_delta(" core idea", collected)

    assert visible == " core idea"


def test_stream_message_does_not_retry_after_partial_text():
    client = OpenAICompatibleClient("test-key")
    attempts = 0

    class _RetryableError(Exception):
        status_code = 503

    async def _fake_stream_once(self, request):
        del request
        nonlocal attempts
        attempts += 1
        yield ApiTextDeltaEvent(text="partial")
        raise _RetryableError("boom")

    client._stream_once = MethodType(_fake_stream_once, client)

    async def _run() -> list[str]:
        request = ApiMessageRequest(model="test-model", messages=[])
        seen: list[str] = []
        with pytest.raises(RequestFailure):
            async for event in client.stream_message(request):
                if isinstance(event, ApiTextDeltaEvent):
                    seen.append(event.text)
        return seen

    seen = asyncio.run(_run())
    assert attempts == 1
    assert seen == ["partial"]


def test_stream_message_retries_when_no_text_emitted():
    client = OpenAICompatibleClient("test-key")
    attempts = 0

    class _RetryableError(Exception):
        status_code = 503

    async def _fake_stream_once(self, request):
        del request
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _RetryableError("transient")
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )

    client._stream_once = MethodType(_fake_stream_once, client)

    async def _run():
        request = ApiMessageRequest(model="test-model", messages=[])
        events = []
        async for event in client.stream_message(request):
            events.append(event)
        return events

    events = asyncio.run(_run())
    assert attempts == 2
    assert any(isinstance(event, ApiRetryEvent) for event in events)
    assert any(isinstance(event, ApiMessageCompleteEvent) for event in events)
