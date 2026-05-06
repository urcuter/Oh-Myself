from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ohmyself.cli import _parse_goal_add_arguments
from ohmyself.services.goal import (
    GoalLimitError,
    append_goal,
    complete_goal,
    format_goals_markdown,
    get_goal_path,
    list_goals,
    stop_goal,
    update_goal_progress,
)


def test_append_goal_records_structured_goal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)

    entry = append_goal("毕业论文", description="日常跟进组会，每周读一篇论文", ends_at=date(2026, 6, 1), progress_percent=20, now=now)

    assert entry.entry_id.startswith("GOAL-20260506-120000-")
    assert entry.path == get_goal_path()
    assert entry.topic == "毕业论文"
    assert entry.description == "日常跟进组会，每周读一篇论文"
    assert entry.ends_at == date(2026, 6, 1)
    assert entry.progress_percent == 20
    assert entry.status == "active"
    assert entry.progress_history[0].progress_percent == 20
    assert entry.progress_history[0].event == "created"
    assert list_goals()[0].entry_id == entry.entry_id


def test_append_goal_enforces_active_goal_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    append_goal("Goal 1", now=now, max_active_goals=1)

    with pytest.raises(GoalLimitError):
        append_goal("Goal 2", now=now, max_active_goals=1)


def test_goal_progress_done_and_stop_update_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    progress_goal = append_goal("Progress goal", now=now)
    stopped_goal = append_goal("Stopped goal", now=now)

    updated = update_goal_progress(progress_goal.entry_id, 100, now=now)
    stopped = stop_goal(stopped_goal.entry_id, now=now)
    completed = complete_goal(stopped_goal.entry_id, now=now)

    assert updated.status == "completed"
    assert updated.progress_percent == 100
    assert updated.closed_at == now
    assert [record.progress_percent for record in updated.progress_history] == [0, 100]
    assert updated.progress_history[-1].event == "progress"
    assert stopped.status == "stopped"
    assert stopped.progress_history[-1].event == "stopped"
    assert completed.status == "completed"
    assert completed.progress_percent == 100
    assert completed.progress_history[-1].event == "completed"


def test_goal_progress_history_survives_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    created_at = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    goal = append_goal("Track history", description="weekly paper", progress_percent=10, now=created_at)

    update_goal_progress(goal.entry_id, 40, now=updated_at)
    reloaded = list_goals()[0]

    assert reloaded.topic == "Track history"
    assert reloaded.description == "weekly paper"
    assert reloaded.progress_percent == 40
    assert [(record.progress_percent, record.event) for record in reloaded.progress_history] == [
        (10, "created"),
        (40, "progress"),
    ]


def test_goal_without_history_gets_imported_history_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    get_goal_path().write_text(
        """{
  "goals": [
    {
      "entry_id": "GOAL-old",
      "content": "Old goal",
      "created_at": "2026-05-06T12:00:00+00:00",
      "progress_percent": 35,
      "ends_at": null,
      "status": "active",
      "updated_at": "2026-05-07T12:00:00+00:00",
      "closed_at": null
    }
  ]
}
""",
        encoding="utf-8",
    )

    reloaded = list_goals()[0]

    assert reloaded.topic == "Old goal"
    assert reloaded.description == ""
    assert reloaded.progress_history[0].progress_percent == 35
    assert reloaded.progress_history[0].event == "imported"


def test_legacy_goal_topic_with_embedded_description_is_normalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    get_goal_path().write_text(
        """{
  "goals": [
    {
      "entry_id": "GOAL-old-2",
      "topic": "强化学习 --每周读论文，跟进组会",
      "created_at": "2026-05-06T12:00:00+00:00",
      "progress_percent": 0,
      "ends_at": null,
      "status": "active",
      "updated_at": "2026-05-06T12:00:00+00:00",
      "closed_at": null
    }
  ]
}
""",
        encoding="utf-8",
    )

    reloaded = list_goals()[0]

    assert reloaded.topic == "强化学习"
    assert reloaded.description == "每周读论文，跟进组会"


def test_format_goals_markdown_groups_by_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    active = append_goal("Active goal", description="Keep shipping", ends_at=date(2026, 6, 1), now=now)
    completed = append_goal("Completed goal", now=now)
    complete_goal(completed.entry_id, now=now)

    rendered = format_goals_markdown()

    assert "# Goals" in rendered
    assert "## Active Goals" in rendered
    assert "## Completed Goals" in rendered
    assert active.entry_id in rendered
    assert "ends 2026-06-01" in rendered
    assert "Keep shipping" in rendered


def test_parse_goal_add_arguments_accepts_options():
    topic, description, ends_at, progress = _parse_goal_add_arguments('毕业论文 --desc "日常跟进组会，每周读一篇论文" --ends 2026-06-01 --progress 30')

    assert topic == "毕业论文"
    assert description == "日常跟进组会，每周读一篇论文"
    assert ends_at == date(2026, 6, 1)
    assert progress == 30


def test_parse_goal_add_arguments_accepts_inline_description():
    topic, description, ends_at, progress = _parse_goal_add_arguments("强化学习：每周读一篇论文")

    assert topic == "强化学习"
    assert description == "每周读一篇论文"
    assert ends_at is None
    assert progress == 0
