from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ohmyself.services.plan import (
    append_plan,
    build_plan_organize_prompt,
    get_plan_inbox_path,
    get_plan_path,
    has_plan_content,
    has_plan_inbox_content,
    parse_plan_content,
    read_plan_inbox,
    read_today_plan,
)


def test_append_plan_writes_simple_inbox_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    now = datetime(2026, 5, 6, 12, 30, 0, tzinfo=timezone.utc)

    entry = append_plan("毕业论文：没搞", now=now)
    inbox_content, inbox_path = read_plan_inbox(now.date())

    assert entry.path == get_plan_path(now.date())
    assert inbox_path == get_plan_inbox_path(now.date())
    assert "- [12:30] 毕业论文：没搞" in inbox_content
    assert "created_at" not in inbox_content
    assert "PLAN-" not in inbox_content
    assert has_plan_inbox_content(now.date())


def test_append_plan_normalizes_topic_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    now = datetime(2026, 5, 6, 12, 30, 0, tzinfo=timezone.utc)
    append_plan("强化学习: 今天读一篇论文", now=now)

    inbox_content, _ = read_plan_inbox(now.date())

    assert "强化学习：今天读一篇论文" in inbox_content


def test_parse_plan_content_supports_both_colons():
    assert parse_plan_content("强化学习：今天读一篇论文") == ("强化学习", "今天读一篇论文")
    assert parse_plan_content("强化学习: 今天没有学下去") == ("强化学习", "今天没有学下去")
    assert parse_plan_content("随手记一下") == (None, "随手记一下")


def test_read_today_plan_uses_display_file_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    path = get_plan_path()
    path.write_text("# Daily Plan - 2026-05-06\n\n## Focus\n- 写作\n", encoding="utf-8")

    content, read_path = read_today_plan()

    assert read_path == path
    assert "## Focus" in content
    assert has_plan_content()


def test_build_plan_organize_prompt_references_inbox_and_display_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    prompt = build_plan_organize_prompt(
        goal_context="- 强化学习: 日常更进组会，每周读一篇论文",
        active_goal_count=5,
        goal_limit=5,
    )

    assert str(get_plan_inbox_path()) in prompt
    assert str(get_plan_path()) in prompt
    assert "Do not include raw metadata" in prompt
    assert "focusing on an existing goal first" in prompt
    assert "grouped together under one shared section" in prompt
    assert "should not be written into the daily plan file" in prompt
    assert "include the warning there instead of putting it in the plan file" in prompt
