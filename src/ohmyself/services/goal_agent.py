from __future__ import annotations

from dataclasses import dataclass, field

from ohmyself.services.goal import GoalEntry, list_goals
from ohmyself.services.goal_memory import (
    ensure_goal_memory_dirs,
    ensure_goal_experience_library,
    format_goal_memory_for_prompt,
    get_goal_experience_dir,
    read_goal_memory,
    append_goal_memory,
)
from ohmyself.services.goal_session import (
    format_recent_sessions_for_prompt,
    link_session_to_goal,
)


@dataclass
class GoalAgentContext:
    active_goal_id: str | None = None
    previous_goal_id: str | None = None
    available_goals: list[GoalEntry] = field(default_factory=list)
    cycle_index: int = 0

    def refresh_goals(self) -> None:
        self.available_goals = [g for g in list_goals() if g.status == "active"]

    def active_goal(self) -> GoalEntry | None:
        if not self.active_goal_id:
            return None
        for goal in self.available_goals:
            if goal.entry_id == self.active_goal_id:
                return goal
        self.available_goals = list_goals()
        for goal in self.available_goals:
            if goal.entry_id == self.active_goal_id:
                return goal
        return None

    def switch_to(self, goal_id: str) -> GoalEntry | None:
        self.refresh_goals()
        for goal in self.available_goals:
            if goal.entry_id == goal_id:
                self.previous_goal_id = self.active_goal_id
                self.active_goal_id = goal_id
                self.cycle_index = self.available_goals.index(goal) if goal in self.available_goals else 0
                ensure_goal_memory_dirs(goal_id)
                ensure_goal_experience_library(goal_id)
                return goal
        return None

    def exit_goal(self) -> None:
        self.previous_goal_id = self.active_goal_id
        self.active_goal_id = None

    def cycle_next(self) -> GoalEntry | None:
        self.refresh_goals()
        if not self.available_goals:
            self.previous_goal_id = self.active_goal_id
            self.active_goal_id = None
            return None

        if self.active_goal_id is None:
            self.previous_goal_id = None
            self.cycle_index = 0
            self.active_goal_id = self.available_goals[0].entry_id
            goal = self.available_goals[0]
            ensure_goal_memory_dirs(goal.entry_id)
            ensure_goal_experience_library(goal.entry_id)
            return goal

        next_index = self.cycle_index + 1
        if next_index >= len(self.available_goals):
            self.previous_goal_id = self.active_goal_id
            self.active_goal_id = None
            self.cycle_index = 0
            return None

        self.previous_goal_id = self.active_goal_id
        self.cycle_index = next_index
        self.active_goal_id = self.available_goals[next_index].entry_id
        goal = self.available_goals[next_index]
        ensure_goal_memory_dirs(goal.entry_id)
        ensure_goal_experience_library(goal.entry_id)
        return goal

    def build_goal_context_prompt(self) -> str:
        goal = self.active_goal()
        if goal is None:
            return ""

        sections: list[str] = []

        sections.append(
            "# 当前专注目标\n"
            f"- 目标: {goal.topic}\n"
            f"- 描述: {goal.description or '(无)'}\n"
            f"- 进度: {goal.progress_percent}%\n"
            f"- 截止: {goal.ends_at.isoformat() if goal.ends_at else '未设置'}\n"
            f"- 状态: {goal.status}"
        )

        memory_section = format_goal_memory_for_prompt(goal.entry_id)
        if memory_section:
            sections.append(memory_section)

        sessions_section = format_recent_sessions_for_prompt(goal.entry_id, limit=3)
        if sessions_section:
            sections.append(sessions_section)

        goal_experience_dir = get_goal_experience_dir(goal.entry_id)
        sections.append(
            "# 目标模式说明\n"
            "当前处于目标专注模式。经验查询(/exper)默认优先搜索本目标的专属经验库"
            f"(`{goal_experience_dir}`)，未找到时自动扩展到全局经验库。\n"
            "会话将自动关联到此目标。使用 `/goal exit` 退出目标模式，按 Tab 键循环切换目标。"
        )

        return "\n\n".join(sections)

    def record_session_link(
        self,
        session_id: str,
        *,
        summary: str = "",
        cwd: str = "",
        model: str = "",
        message_count: int = 0,
    ) -> None:
        if self.active_goal_id:
            link_session_to_goal(
                self.active_goal_id,
                session_id,
                summary=summary,
                cwd=cwd,
                model=model,
                message_count=message_count,
            )
