from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ohmyself.config.paths import get_home_dir

LONG_PLAN_FILENAME = "long_plan.json"


def _get_long_plan_path() -> Path:
    return get_home_dir() / LONG_PLAN_FILENAME


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class SpecialDate:
    date: str  # YYYY-MM-DD
    type: str  # "rest" | "work" | "focus"
    label: str

    def to_dict(self) -> dict:
        return {"date": self.date, "type": self.type, "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> SpecialDate:
        return cls(date=d.get("date", ""), type=d.get("type", "rest"), label=d.get("label", ""))


@dataclass
class Milestone:
    id: str
    name: str
    target_date: str  # YYYY-MM-DD
    original_target_date: str  # YYYY-MM-DD, snapshot at plan creation
    success_criteria: str
    status: str = "pending"  # pending | in_progress | completed
    linked_goal_id: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "target_date": self.target_date,
            "original_target_date": self.original_target_date,
            "success_criteria": self.success_criteria,
            "status": self.status,
            "linked_goal_id": self.linked_goal_id,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Milestone:
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            target_date=d.get("target_date", ""),
            original_target_date=d.get("original_target_date", d.get("target_date", "")),
            success_criteria=d.get("success_criteria", ""),
            status=d.get("status", "pending"),
            linked_goal_id=d.get("linked_goal_id"),
            completed_at=d.get("completed_at"),
        )


@dataclass
class Phase:
    id: str
    name: str
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    theme: str
    original_start_date: str
    original_end_date: str
    milestones: list[Milestone] = field(default_factory=list)
    status: str = "pending"  # pending | active | completed

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "theme": self.theme,
            "original_start_date": self.original_start_date,
            "original_end_date": self.original_end_date,
            "milestones": [m.to_dict() for m in self.milestones],
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Phase:
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            start_date=d.get("start_date", ""),
            end_date=d.get("end_date", ""),
            theme=d.get("theme", ""),
            original_start_date=d.get("original_start_date", d.get("start_date", "")),
            original_end_date=d.get("original_end_date", d.get("end_date", "")),
            milestones=[Milestone.from_dict(m) for m in d.get("milestones", [])],
            status=d.get("status", "pending"),
        )


@dataclass
class LongPlanConfig:
    enabled: bool = False
    horizon_months: int = 3
    work_days: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])  # 1=Mon..7=Sun
    daily_focus_start: int = 9
    daily_focus_end: int = 18
    review_cadence: str = "weekly"  # weekly | biweekly | monthly
    auto_adapt: bool = True
    special_dates: list[SpecialDate] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "horizon_months": self.horizon_months,
            "work_days": self.work_days,
            "daily_focus_start": self.daily_focus_start,
            "daily_focus_end": self.daily_focus_end,
            "review_cadence": self.review_cadence,
            "auto_adapt": self.auto_adapt,
            "special_dates": [s.to_dict() for s in self.special_dates],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LongPlanConfig:
        return cls(
            enabled=d.get("enabled", False),
            horizon_months=d.get("horizon_months", 3),
            work_days=d.get("work_days", [1, 2, 3, 4, 5]),
            daily_focus_start=d.get("daily_focus_start", 9),
            daily_focus_end=d.get("daily_focus_end", 18),
            review_cadence=d.get("review_cadence", "weekly"),
            auto_adapt=d.get("auto_adapt", True),
            special_dates=[SpecialDate.from_dict(s) for s in d.get("special_dates", [])],
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class DailyExecutionRecord:
    date: str  # YYYY-MM-DD
    planned_milestone_ids: list[str] = field(default_factory=list)
    completed_milestone_ids: list[str] = field(default_factory=list)
    partial_milestone_ids: list[str] = field(default_factory=list)
    completion_score: float = 0.0
    blocker: str | None = None
    energy_note: str | None = None
    ai_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "planned_milestone_ids": self.planned_milestone_ids,
            "completed_milestone_ids": self.completed_milestone_ids,
            "partial_milestone_ids": self.partial_milestone_ids,
            "completion_score": self.completion_score,
            "blocker": self.blocker,
            "energy_note": self.energy_note,
            "ai_summary": self.ai_summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DailyExecutionRecord:
        return cls(
            date=d.get("date", ""),
            planned_milestone_ids=d.get("planned_milestone_ids", []),
            completed_milestone_ids=d.get("completed_milestone_ids", []),
            partial_milestone_ids=d.get("partial_milestone_ids", []),
            completion_score=d.get("completion_score", 0.0),
            blocker=d.get("blocker"),
            energy_note=d.get("energy_note"),
            ai_summary=d.get("ai_summary", ""),
        )


@dataclass
class RhythmProfile:
    avg_completion_rate: float = 0.0
    weekly_throughput: float = 0.0
    best_working_days: list[int] = field(default_factory=list)
    worst_working_days: list[int] = field(default_factory=list)
    typical_daily_capacity: float = 0.0
    delay_days_accumulated: int = 0
    trend: str = "stable"  # improving | stable | declining
    last_analyzed_at: str = ""
    analysis_narrative: str = ""

    def to_dict(self) -> dict:
        return {
            "avg_completion_rate": self.avg_completion_rate,
            "weekly_throughput": self.weekly_throughput,
            "best_working_days": self.best_working_days,
            "worst_working_days": self.worst_working_days,
            "typical_daily_capacity": self.typical_daily_capacity,
            "delay_days_accumulated": self.delay_days_accumulated,
            "trend": self.trend,
            "last_analyzed_at": self.last_analyzed_at,
            "analysis_narrative": self.analysis_narrative,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RhythmProfile:
        return cls(
            avg_completion_rate=d.get("avg_completion_rate", 0.0),
            weekly_throughput=d.get("weekly_throughput", 0.0),
            best_working_days=d.get("best_working_days", []),
            worst_working_days=d.get("worst_working_days", []),
            typical_daily_capacity=d.get("typical_daily_capacity", 0.0),
            delay_days_accumulated=d.get("delay_days_accumulated", 0),
            trend=d.get("trend", "stable"),
            last_analyzed_at=d.get("last_analyzed_at", ""),
            analysis_narrative=d.get("analysis_narrative", ""),
        )


@dataclass
class LongPlan:
    config: LongPlanConfig = field(default_factory=LongPlanConfig)
    phases: list[Phase] = field(default_factory=list)
    execution_history: list[DailyExecutionRecord] = field(default_factory=list)
    rhythm_profile: RhythmProfile | None = None
    last_reviewed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "phases": [p.to_dict() for p in self.phases],
            "execution_history": [r.to_dict() for r in self.execution_history],
            "rhythm_profile": self.rhythm_profile.to_dict() if self.rhythm_profile else None,
            "last_reviewed_at": self.last_reviewed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LongPlan:
        rp = d.get("rhythm_profile")
        return cls(
            config=LongPlanConfig.from_dict(d.get("config", {})),
            phases=[Phase.from_dict(p) for p in d.get("phases", [])],
            execution_history=[DailyExecutionRecord.from_dict(r) for r in d.get("execution_history", [])],
            rhythm_profile=RhythmProfile.from_dict(rp) if rp else None,
            last_reviewed_at=d.get("last_reviewed_at", ""),
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LongPlanService:
    """Manages the long-term calendar plan and rhythm tracking."""

    def __init__(self) -> None:
        self._plan: LongPlan = LongPlan()
        self._load()

    # --- Persistence ---

    def _load(self) -> None:
        path = _get_long_plan_path()
        if not path.exists():
            self._plan = LongPlan()
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._plan = LongPlan.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            self._plan = LongPlan()

    def _save(self) -> None:
        path = _get_long_plan_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # --- Config ---

    def get_config(self) -> LongPlanConfig:
        return self._plan.config

    def update_config(self, **kwargs: object) -> LongPlanConfig:
        cfg = self._plan.config
        for key, value in kwargs.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        cfg.updated_at = _now_iso()
        self._save()
        return cfg

    def is_enabled(self) -> bool:
        return self._plan.config.enabled

    def enable(self) -> None:
        cfg = self._plan.config
        cfg.enabled = True
        if not cfg.created_at:
            cfg.created_at = _now_iso()
        cfg.updated_at = _now_iso()
        self._save()

    def disable(self) -> None:
        self._plan.config.enabled = False
        self._plan.config.updated_at = _now_iso()
        self._save()

    # --- Phases ---

    def get_phases(self) -> list[Phase]:
        return list(self._plan.phases)

    def get_active_phase(self) -> Phase | None:
        today = _today()
        for phase in self._plan.phases:
            if phase.start_date <= today <= phase.end_date and phase.status != "completed":
                return phase
        # Fallback: first non-completed phase
        for phase in self._plan.phases:
            if phase.status != "completed":
                return phase
        return None

    def add_phase(self, phase: Phase) -> None:
        self._plan.phases.append(phase)
        self._save()

    def update_phase(self, phase_id: str, **kwargs: object) -> Phase | None:
        for phase in self._plan.phases:
            if phase.id == phase_id:
                for key, value in kwargs.items():
                    if hasattr(phase, key):
                        setattr(phase, key, value)
                self._save()
                return phase
        return None

    def replace_phases(self, phases: list[Phase]) -> None:
        self._plan.phases = phases
        self._save()

    # --- Milestones ---

    def get_upcoming_milestones(self, n_days: int = 14) -> list[Milestone]:
        today = _today()
        cutoff = (date.today() + timedelta(days=n_days)).isoformat()
        result: list[Milestone] = []
        for phase in self._plan.phases:
            if phase.status == "completed":
                continue
            for m in phase.milestones:
                if m.status == "completed":
                    continue
                if today <= m.target_date <= cutoff:
                    result.append(m)
        result.sort(key=lambda m: m.target_date)
        return result

    def get_today_milestones(self) -> list[Milestone]:
        today = _today()
        result: list[Milestone] = []
        for phase in self._plan.phases:
            if phase.status == "completed":
                continue
            for m in phase.milestones:
                if m.status == "completed":
                    continue
                # Milestones due today or overdue
                if m.target_date <= today:
                    result.append(m)
        result.sort(key=lambda m: m.target_date)
        return result

    def get_all_milestones(self) -> list[tuple[Phase, Milestone]]:
        result: list[tuple[Phase, Milestone]] = []
        for phase in self._plan.phases:
            for m in phase.milestones:
                result.append((phase, m))
        return result

    def find_milestone(self, milestone_id: str) -> tuple[Phase | None, Milestone | None]:
        for phase in self._plan.phases:
            for m in phase.milestones:
                if m.id == milestone_id:
                    return phase, m
        return None, None

    # --- Work-day calculation ---

    def is_work_day(self, d: date | None = None) -> bool:
        """Check if a date is a work day (respects weekly cycle + special dates)."""
        target = d or date.today()
        date_str = target.isoformat()
        cfg = self._plan.config
        # Special dates override the weekly cycle
        for sd in cfg.special_dates:
            if sd.date == date_str:
                return sd.type != "rest"
        # ISO weekday: 1=Mon..7=Sun
        iso_weekday = target.isoweekday()
        return iso_weekday in cfg.work_days

    def count_work_days(self, start: str, end: str) -> int:
        """Count work days between two dates (inclusive)."""
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if start_date > end_date:
            return 0
        count = 0
        current = start_date
        while current <= end_date:
            if self.is_work_day(current):
                count += 1
            current += timedelta(days=1)
        return count

    # --- Execution history ---

    def add_execution_record(self, record: DailyExecutionRecord) -> None:
        # Replace existing record for same date
        for i, existing in enumerate(self._plan.execution_history):
            if existing.date == record.date:
                self._plan.execution_history[i] = record
                self._save()
                return
        self._plan.execution_history.append(record)
        self._save()

    def get_recent_records(self, n_days: int = 14) -> list[DailyExecutionRecord]:
        cutoff = (date.today() - timedelta(days=n_days)).isoformat()
        return [r for r in self._plan.execution_history if r.date >= cutoff]

    def get_execution_history(self) -> list[DailyExecutionRecord]:
        return list(self._plan.execution_history)

    def get_rhythm_profile(self) -> RhythmProfile | None:
        return self._plan.rhythm_profile

    def get_record_for_date(self, d: str) -> DailyExecutionRecord | None:
        for record in self._plan.execution_history:
            if record.date == d:
                return record
        return None

    # --- Rhythm analysis ---

    def compute_rhythm_profile(self) -> RhythmProfile:
        """Compute rhythm profile from execution history."""
        history = self._plan.execution_history
        if len(history) < 3:
            return RhythmProfile(last_analyzed_at=_now_iso())

        scores = [r.completion_score for r in history]
        avg_rate = sum(scores) / len(scores)

        # Weekly throughput: milestones completed per week
        if len(history) >= 7:
            total_completed = sum(len(r.completed_milestone_ids) for r in history)
            weeks = max(1, len(history) / 7.0)
            weekly_throughput = total_completed / weeks
        else:
            total_completed = sum(len(r.completed_milestone_ids) for r in history)
            weekly_throughput = total_completed  # partial week

        # Best / worst working days: group scores by day of week
        day_scores: dict[int, list[float]] = {}
        for r in history:
            try:
                d = date.fromisoformat(r.date)
                dow = d.isoweekday()
                day_scores.setdefault(dow, []).append(r.completion_score)
            except ValueError:
                continue

        day_averages = {dow: sum(vals) / len(vals) for dow, vals in day_scores.items() if vals}
        sorted_days = sorted(day_averages.items(), key=lambda x: x[1], reverse=True)
        best_days = [d for d, _ in sorted_days[:3] if day_averages[d] >= 0.5]
        worst_days = [d for d, _ in sorted_days[-3:] if day_averages[d] < 0.5]

        # Typical daily capacity: avg milestones completed+partial per day
        capacities = [len(r.completed_milestone_ids) + len(r.partial_milestone_ids) for r in history]
        typical_capacity = sum(capacities) / len(capacities)

        # Delay days accumulated
        delay = self.get_plan_deviation()

        # Trend: compare first half vs second half
        mid = len(history) // 2
        first_half_avg = sum(scores[:mid]) / mid if mid > 0 else avg_rate
        second_half_avg = sum(scores[mid:]) / (len(history) - mid) if (len(history) - mid) > 0 else avg_rate
        if second_half_avg >= first_half_avg + 0.1:
            trend = "improving"
        elif second_half_avg <= first_half_avg - 0.1:
            trend = "declining"
        else:
            trend = "stable"

        return RhythmProfile(
            avg_completion_rate=round(avg_rate, 2),
            weekly_throughput=round(weekly_throughput, 2),
            best_working_days=best_days,
            worst_working_days=worst_days,
            typical_daily_capacity=round(typical_capacity, 2),
            delay_days_accumulated=delay,
            trend=trend,
            last_analyzed_at=_now_iso(),
            analysis_narrative="",
        )

    def get_plan_deviation(self) -> int:
        """Return delay days: positive = behind schedule, negative = ahead."""
        today = date.today()
        delay = 0
        for phase in self._plan.phases:
            for m in phase.milestones:
                try:
                    target = date.fromisoformat(m.target_date)
                except ValueError:
                    continue
                if m.status == "completed" and m.completed_at:
                    try:
                        actual = date.fromisoformat(m.completed_at)
                    except ValueError:
                        continue
                    diff = (actual - target).days
                    if diff > 0:
                        delay += diff
                    elif diff < 0:
                        delay += diff  # negative = ahead
                elif m.status != "completed" and target < today:
                    delay += (today - target).days
        return delay

    # --- Formatters ---

    def format_for_system_prompt(self) -> str:
        """Generate the system prompt section for the long-term plan."""
        cfg = self._plan.config
        if not cfg.enabled:
            return ""

        lines = ["## 你的长期日程计划"]

        active_phase = self.get_active_phase()
        if active_phase:
            phase_dates = f"{active_phase.start_date} ~ {active_phase.end_date}"
            lines.append(f"当前阶段: {active_phase.name} ({phase_dates})")
            if active_phase.theme:
                lines.append(f"阶段主题: {active_phase.theme}")

        upcoming = self.get_upcoming_milestones(14)
        if upcoming:
            lines.append("近期里程碑:")
            for m in upcoming[:8]:
                status_label = {"pending": "待开始", "in_progress": "进行中", "completed": "已完成"}.get(m.status, m.status)
                lines.append(f"  - [{m.target_date}] {m.name} ({status_label})")

        # Rhythm summary
        rp = self._plan.rhythm_profile
        if rp and rp.last_analyzed_at:
            trend_label = {"improving": "提升中", "stable": "稳定", "declining": "下降中"}.get(rp.trend, rp.trend)
            rate_pct = int(rp.avg_completion_rate * 100)
            delay_str = f"超前 {-rp.delay_days_accumulated} 天" if rp.delay_days_accumulated < 0 else f"滞后 {rp.delay_days_accumulated} 天" if rp.delay_days_accumulated > 0 else "完全按计划"
            lines.append(f"节奏概况: 近2周完成率 {rate_pct}%，{delay_str}，趋势{trend_label}")

        # Today info
        lines.append(f"今日是{'工作日' if self.is_work_day() else '休息日'}，专注时段 {cfg.daily_focus_start}:00-{cfg.daily_focus_end}:00")

        return "\n".join(lines)

    def format_for_daily_plan(self) -> str:
        """Generate context for the daily plan generation prompt."""
        cfg = self._plan.config
        if not cfg.enabled:
            return ""

        parts: list[str] = []
        active_phase = self.get_active_phase()
        if active_phase:
            parts.append(f"当前阶段: {active_phase.name} ({active_phase.start_date} ~ {active_phase.end_date})")
            parts.append(f"阶段主题: {active_phase.theme}")

        upcoming = self.get_upcoming_milestones(7)
        if upcoming:
            parts.append("本周应推进的里程碑:")
            for m in upcoming[:5]:
                parts.append(f"  - [{m.target_date}] {m.name} — {m.success_criteria}")

        rp = self._plan.rhythm_profile
        if rp and rp.last_analyzed_at:
            parts.append(f"参考节奏: 日均完成 {rp.typical_daily_capacity} 个里程碑，请据此合理分配今日任务量")

        return "\n".join(parts)

    def format_rhythm_summary(self) -> str:
        """Human-readable rhythm summary for CLI display."""
        rp = self._plan.rhythm_profile
        if not rp or not rp.last_analyzed_at:
            return "暂无节奏数据（需要至少 3 天的执行记录）"

        trend_label = {"improving": "提升中 ↑", "stable": "稳定 →", "declining": "下降中 ↓"}.get(rp.trend, rp.trend)
        rate_pct = int(rp.avg_completion_rate * 100)

        day_names = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
        best_str = ", ".join(day_names.get(d, str(d)) for d in rp.best_working_days[:3]) if rp.best_working_days else "数据不足"
        worst_str = ", ".join(day_names.get(d, str(d)) for d in rp.worst_working_days[:3]) if rp.worst_working_days else "数据不足"

        delay_str = (
            f"超前 {-rp.delay_days_accumulated} 天" if rp.delay_days_accumulated < 0
            else f"滞后 {rp.delay_days_accumulated} 天" if rp.delay_days_accumulated > 0
            else "完全按计划推进"
        )

        lines = [
            f"完成率: {rate_pct}%（趋势: {trend_label}）",
            f"周均完成: {rp.weekly_throughput} 个里程碑 | 日均产能: {rp.typical_daily_capacity} 个",
            f"进度偏差: {delay_str}",
            f"最高效日: {best_str} | 最低效日: {worst_str}",
        ]
        if rp.analysis_narrative:
            lines.append(f"\n分析: {rp.analysis_narrative}")
        return "\n".join(lines)

    def format_plan_summary(self) -> str:
        """Full plan overview for CLI display."""
        cfg = self._plan.config
        if not cfg.enabled:
            return "长期计划功能未启用。使用 /longplan setup 开始设定。"

        lines = [f"计划周期: {cfg.horizon_months} 个月 | 审视频率: {cfg.review_cadence}"]
        lines.append(f"工作日: {self._format_days(cfg.work_days)} | 专注时段: {cfg.daily_focus_start}:00-{cfg.daily_focus_end}:00")

        if cfg.special_dates:
            lines.append(f"特殊日期: {len(cfg.special_dates)} 个")
            for sd in cfg.special_dates[:5]:
                type_label = {"rest": "休息", "work": "工作日", "focus": "专注日"}.get(sd.type, sd.type)
                lines.append(f"  - {sd.date} {type_label}: {sd.label}")

        lines.append("")

        for phase in self._plan.phases:
            status_label = {"pending": "待开始", "active": "进行中", "completed": "已完成"}.get(phase.status, phase.status)
            lines.append(f"## {phase.name} [{status_label}]")
            lines.append(f"  {phase.start_date} ~ {phase.end_date} | 主题: {phase.theme}")
            lines.append(f"  里程碑: {len(phase.milestones)} 个")
            for m in phase.milestones:
                ms = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(m.status, "?")
                lines.append(f"    {ms} [{m.target_date}] {m.name}")
                if m.success_criteria:
                    lines.append(f"       标准: {m.success_criteria}")
            lines.append("")

        if self._plan.last_reviewed_at:
            lines.append(f"上次审视: {self._plan.last_reviewed_at}")

        return "\n".join(lines)

    @staticmethod
    def _format_days(days: list[int]) -> str:
        names = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
        return "周" + "/".join(names.get(d, str(d)) for d in sorted(days))

    # --- Milestone helpers ---

    def mark_milestone_status(self, milestone_id: str, status: str, completed_at: str | None = None) -> bool:
        """Update a milestone's status. Returns True if found."""
        _, m = self.find_milestone(milestone_id)
        if m is None:
            return False
        m.status = status
        if status == "completed":
            m.completed_at = completed_at or _today()
        self._save()
        return True

    def new_milestone_id(self) -> str:
        return uuid4().hex[:12]

    def new_phase_id(self) -> str:
        return uuid4().hex[:12]
