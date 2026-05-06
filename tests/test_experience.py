from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ohmyself.services.experience import (
    append_experience,
    build_experience_answer_prompt,
    build_experience_organize_prompt,
    build_experience_retrieval_task,
    get_default_experience_path,
    get_experience_dir,
    has_experience_content,
)


def test_append_experience_creates_default_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    entry = append_experience("先确认目标，再讨论实现。", now=datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc))

    assert entry.entry_id.startswith("EXP-20260506-120000-")
    assert entry.path == get_default_experience_path()
    text = entry.path.read_text(encoding="utf-8")
    assert "# Default Experience Library" in text
    assert entry.entry_id in text
    assert "- source: /exper add" in text
    assert "先确认目标，再讨论实现。" in text
    assert has_experience_content()


def test_experience_retrieval_task_is_scoped_to_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    prompt = build_experience_retrieval_task("我遇到沟通分歧时应该怎么办？")

    assert str(get_experience_dir()) in prompt
    assert "experience_retriever" in prompt
    assert "glob" in prompt
    assert "read_file" in prompt
    assert "grep" in prompt
    assert "不要回答用户应该怎么做" in prompt


def test_experience_answer_prompt_uses_retrieval_report():
    prompt = build_experience_answer_prompt("我应该怎么沟通？", "EXP-1: 先确认目标。")

    assert "EXP-1: 先确认目标。" in prompt
    assert "经验库证据" in prompt
    assert "不要再次调用 subagent" in prompt


def test_experience_organize_prompt_is_non_destructive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    prompt = build_experience_organize_prompt()

    assert str(get_default_experience_path()) in prompt
    assert "非破坏性整理" in prompt
    assert "不要删除 default.md" in prompt
