from __future__ import annotations

from ohmyself.api.openai_client import _normalize_visible_delta


def test_normalize_visible_delta_handles_cumulative_chunks():
    collected = ""
    raw_chunks = [
        "好，这几个概念",
        "好，这几个概念正好适合动手做一次",
        "好，这几个概念正好适合动手做一次就搞明白。",
    ]

    visible_chunks = []
    for chunk in raw_chunks:
        visible = _normalize_visible_delta(chunk, collected)
        visible_chunks.append(visible)
        collected += visible

    assert visible_chunks == [
        "好，这几个概念",
        "正好适合动手做一次",
        "就搞明白。",
    ]
    assert collected == raw_chunks[-1]


def test_normalize_visible_delta_preserves_incremental_chunks():
    collected = "好，这几个概念"

    visible = _normalize_visible_delta("正好适合动手做一次", collected)

    assert visible == "正好适合动手做一次"
