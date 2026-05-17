from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ohmyself.services.long_plan import (
    DailyExecutionRecord,
    LongPlanConfig,
    LongPlanService,
    Milestone,
    Phase,
    RhythmProfile,
    SpecialDate,
)


class TestLongPlanConfig:
    def test_default_config(self):
        cfg = LongPlanConfig()
        assert cfg.enabled is False
        assert cfg.horizon_months == 3
        assert cfg.work_days == [1, 2, 3, 4, 5]
        assert cfg.daily_focus_start == 9
        assert cfg.daily_focus_end == 18
        assert cfg.review_cadence == "weekly"
        assert cfg.auto_adapt is True
        assert cfg.special_dates == []

    def test_config_serialization_roundtrip(self):
        cfg = LongPlanConfig(
            enabled=True,
            horizon_months=6,
            work_days=[1, 2, 3, 4],
            special_dates=[SpecialDate(date="2026-06-01", type="rest", label="儿童节")],
        )
        data = cfg.to_dict()
        restored = LongPlanConfig.from_dict(data)
        assert restored.enabled == cfg.enabled
        assert restored.horizon_months == cfg.horizon_months
        assert restored.work_days == cfg.work_days
        assert len(restored.special_dates) == 1
        assert restored.special_dates[0].label == "儿童节"


class TestLongPlanService:
    def test_new_service_has_default_plan(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        assert not svc.is_enabled()
        assert svc.get_phases() == []
        assert svc.get_execution_history() == []
        assert svc.get_rhythm_profile() is None

    def test_enable_disable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        svc.enable()
        assert svc.is_enabled()
        cfg = svc.get_config()
        assert cfg.created_at != ""

        svc.disable()
        assert not svc.is_enabled()

    def test_persistence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        svc.enable()
        svc.update_config(horizon_months=6, daily_focus_start=8)

        # New instance should load from disk
        svc2 = LongPlanService()
        assert svc2.is_enabled()
        assert svc2.get_config().horizon_months == 6
        assert svc2.get_config().daily_focus_start == 8

    def test_update_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        svc.update_config(
            review_cadence="biweekly",
            auto_adapt=False,
        )
        cfg = svc.get_config()
        assert cfg.review_cadence == "biweekly"
        assert cfg.auto_adapt is False


class TestWorkDayCalculation:
    def test_is_work_day_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        # Monday is work day
        assert svc.is_work_day(date(2026, 5, 18)) is True  # Monday
        assert svc.is_work_day(date(2026, 5, 19)) is True  # Tuesday
        assert svc.is_work_day(date(2026, 5, 23)) is False  # Saturday
        assert svc.is_work_day(date(2026, 5, 24)) is False  # Sunday

    def test_special_date_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        svc.update_config(
            special_dates=[
                SpecialDate(date="2026-05-19", type="rest", label="休息"),
                SpecialDate(date="2026-05-23", type="work", label="补班"),
            ]
        )
        # Tuesday overridden to rest
        assert svc.is_work_day(date(2026, 5, 19)) is False
        # Saturday overridden to work
        assert svc.is_work_day(date(2026, 5, 23)) is True

    def test_count_work_days(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        # Mon-Fri should be 5 work days
        count = svc.count_work_days("2026-05-18", "2026-05-24")
        assert count == 5

    def test_count_work_days_reversed_dates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        count = svc.count_work_days("2026-05-24", "2026-05-18")
        assert count == 0


class TestPhaseAndMilestone:
    def test_add_and_query_phase(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        svc.enable()

        phase = Phase(
            id=svc.new_phase_id(),
            name="Phase 1",
            start_date="2026-05-18",
            end_date="2026-06-18",
            theme="Foundation",
            original_start_date="2026-05-18",
            original_end_date="2026-06-18",
            milestones=[
                Milestone(
                    id=svc.new_milestone_id(),
                    name="MS1",
                    target_date="2026-05-25",
                    original_target_date="2026-05-25",
                    success_criteria="Done",
                    status="pending",
                )
            ],
            status="active",
        )
        svc.add_phase(phase)

        phases = svc.get_phases()
        assert len(phases) == 1
        assert phases[0].name == "Phase 1"

        active = svc.get_active_phase()
        assert active is not None
        assert active.name == "Phase 1"

    def test_get_upcoming_milestones(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        svc.enable()

        today = date.today().isoformat()
        future = (date.today() + timedelta(days=5)).isoformat()
        far = (date.today() + timedelta(days=30)).isoformat()

        phase = Phase(
            id=svc.new_phase_id(),
            name="Phase",
            start_date=today,
            end_date=far,
            theme="Test",
            original_start_date=today,
            original_end_date=far,
            milestones=[
                Milestone(
                    id=svc.new_milestone_id(),
                    name="Due soon",
                    target_date=future,
                    original_target_date=future,
                    success_criteria="Done",
                    status="pending",
                ),
                Milestone(
                    id=svc.new_milestone_id(),
                    name="Far away",
                    target_date=far,
                    original_target_date=far,
                    success_criteria="Done",
                    status="pending",
                ),
            ],
            status="active",
        )
        svc.add_phase(phase)

        upcoming = svc.get_upcoming_milestones(14)
        assert len(upcoming) == 1
        assert upcoming[0].name == "Due soon"

    def test_mark_milestone_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()

        ms_id = svc.new_milestone_id()
        phase = Phase(
            id=svc.new_phase_id(),
            name="Phase",
            start_date="2026-05-01", end_date="2026-06-01",
            theme="T", original_start_date="2026-05-01", original_end_date="2026-06-01",
            milestones=[Milestone(
                id=ms_id, name="M", target_date="2026-05-15",
                original_target_date="2026-05-15", success_criteria="Done", status="pending",
            )],
            status="active",
        )
        svc.add_phase(phase)

        assert svc.mark_milestone_status(ms_id, "completed")
        _, m = svc.find_milestone(ms_id)
        assert m.status == "completed"
        assert m.completed_at is not None

        # Non-existent
        assert not svc.mark_milestone_status("nonexistent", "completed")

    def test_update_phase(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()

        pid = svc.new_phase_id()
        phase = Phase(
            id=pid, name="Old", start_date="2026-05-01", end_date="2026-06-01",
            theme="T", original_start_date="2026-05-01", original_end_date="2026-06-01",
            milestones=[], status="active",
        )
        svc.add_phase(phase)

        updated = svc.update_phase(pid, name="New", status="completed")
        assert updated is not None
        assert updated.name == "New"
        assert updated.status == "completed"

        # Non-existent
        assert svc.update_phase("nonexistent", name="X") is None


class TestExecutionHistory:
    def test_add_and_query_records(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()

        record = DailyExecutionRecord(
            date="2026-05-17",
            planned_milestone_ids=["a", "b"],
            completed_milestone_ids=["a"],
            completion_score=0.5,
            ai_summary="Half done",
        )
        svc.add_execution_record(record)

        history = svc.get_execution_history()
        assert len(history) == 1
        assert history[0].completion_score == 0.5

        # Replace existing record for same date
        record2 = DailyExecutionRecord(
            date="2026-05-17",
            planned_milestone_ids=["a", "b"],
            completed_milestone_ids=["a", "b"],
            completion_score=1.0,
            ai_summary="All done",
        )
        svc.add_execution_record(record2)
        history = svc.get_execution_history()
        assert len(history) == 1  # Still 1, replaced
        assert history[0].completion_score == 1.0

    def test_get_recent_records(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()

        today = date.today()
        for i in range(20):
            d = today - timedelta(days=i)
            svc.add_execution_record(DailyExecutionRecord(
                date=d.isoformat(),
                completion_score=0.5,
                ai_summary=f"Day {i}",
            ))

        recent = svc.get_recent_records(7)
        assert len(recent) == 8  # today + 7 prior days

        recent14 = svc.get_recent_records(14)
        assert len(recent14) == 15  # today + 14 prior days

    def test_get_record_for_date(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()

        svc.add_execution_record(DailyExecutionRecord(
            date="2026-05-17", completion_score=0.8, ai_summary="Good",
        ))

        r = svc.get_record_for_date("2026-05-17")
        assert r is not None
        assert r.completion_score == 0.8

        assert svc.get_record_for_date("2026-01-01") is None


class TestRhythmProfile:
    def setup_method(self):
        self.today = date.today()

    def _make_service_with_history(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        # Add records for Monday through Friday of last week
        for i in range(6, 1, -1):  # Monday to Friday (6 days ago to 2 days ago)
            d = self.today - timedelta(days=i)
            svc.add_execution_record(DailyExecutionRecord(
                date=d.isoformat(),
                planned_milestone_ids=[f"m{i}"],
                completed_milestone_ids=[f"m{i}"],
                completion_score=1.0,
                ai_summary="Completed",
            ))
        return svc

    def test_empty_history_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        rp = svc.compute_rhythm_profile()
        assert rp.avg_completion_rate == 0.0
        assert rp.trend == "stable"

    def test_compute_rhythm_profile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        svc = self._make_service_with_history(tmp_path, monkeypatch)
        rp = svc.compute_rhythm_profile()

        # All scores are 1.0
        assert rp.avg_completion_rate == 1.0
        assert rp.weekly_throughput > 0
        assert rp.trend == "stable"
        assert rp.last_analyzed_at != ""

    def test_plan_deviation_with_no_phases(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        assert svc.get_plan_deviation() == 0

    def test_plan_deviation_overdue(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()

        ms_id = svc.new_milestone_id()
        # A milestone due 10 days ago, not completed
        overdue_date = (date.today() - timedelta(days=10)).isoformat()
        phase = Phase(
            id=svc.new_phase_id(),
            name="Phase", start_date="2026-01-01", end_date="2026-12-31",
            theme="T", original_start_date="2026-01-01", original_end_date="2026-12-31",
            milestones=[Milestone(
                id=ms_id, name="Overdue", target_date=overdue_date,
                original_target_date=overdue_date,
                success_criteria="Done", status="pending",
            )],
            status="active",
        )
        svc.add_phase(phase)

        deviation = svc.get_plan_deviation()
        assert deviation >= 10  # At least 10 days behind


class TestFormatting:
    def test_format_for_system_prompt_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        assert svc.format_for_system_prompt() == ""

    def test_format_for_system_prompt_enabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        svc.enable()

        result = svc.format_for_system_prompt()
        assert "长期日程计划" in result
        assert "休息日" in result or "工作日" in result

    def test_format_for_daily_plan_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        assert svc.format_for_daily_plan() == ""

    def test_format_rhythm_summary_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        result = svc.format_rhythm_summary()
        assert "3" in result  # "至少需要 3 天"

    def test_format_plan_summary_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        result = svc.format_plan_summary()
        assert "未启用" in result


class TestSerialization:
    def test_long_plan_serialization_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
        svc = LongPlanService()
        svc.enable()
        svc.update_config(horizon_months=6)

        phase = Phase(
            id=svc.new_phase_id(), name="P1",
            start_date="2026-05-01", end_date="2026-06-01",
            theme="Test", original_start_date="2026-05-01", original_end_date="2026-06-01",
            milestones=[Milestone(
                id=svc.new_milestone_id(), name="M1",
                target_date="2026-05-15", original_target_date="2026-05-15",
                success_criteria="Done", status="pending",
            )],
            status="active",
        )
        svc.add_phase(phase)

        svc.add_execution_record(DailyExecutionRecord(
            date="2026-05-17", completion_score=0.8, ai_summary="Good",
        ))

        # Reload from disk
        svc2 = LongPlanService()
        assert svc2.is_enabled()
        assert svc2.get_config().horizon_months == 6
        assert len(svc2.get_phases()) == 1
        assert svc2.get_phases()[0].name == "P1"
        assert len(svc2.get_execution_history()) == 1
