from __future__ import annotations

from pydantic import BaseModel, Field

from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ScheduleTaskInput(BaseModel):
    action: str = Field(description="Action: 'schedule_os' (OS-level reminder via Windows Task Scheduler, fires even when ohmy is closed), 'schedule_shutdown' (on OS shutdown detection), 'list' (view all tasks), 'cancel' (remove by ID)")
    cron: str | None = Field(default=None, description="Standard 5-field cron expression (e.g. '0 8 * * *'). Required for 'schedule_os' action.")
    prompt: str | None = Field(default=None, description="The reminder text or task prompt. Required for 'schedule_os' and 'schedule_shutdown' actions.")
    task_id: str | None = Field(default=None, description="Task ID to cancel. Required for 'cancel' action.")


class ScheduleTaskTool(BaseTool):
    name = "schedule_task"
    description = "Schedule OS-level reminders (Windows Task Scheduler, fires even when ohmy is closed) and shutdown hooks. Also list and cancel tasks."
    input_model = ScheduleTaskInput

    async def execute(self, arguments: ScheduleTaskInput, context: ToolExecutionContext) -> ToolResult:
        scheduler = context.metadata.get("scheduler")
        if scheduler is None:
            return ToolResult(output="Scheduler service is not available in this session.", is_error=True)

        try:
            if arguments.action == "schedule_os":
                if not arguments.cron or not arguments.prompt:
                    return ToolResult(output="'cron' and 'prompt' are required for 'schedule_os' action.", is_error=True)
                task = scheduler.schedule_on_os(arguments.cron, arguments.prompt)
                return ToolResult(output=f"OS reminder registered.\nID: {task.id}\nCron: {task.cron_expr}\nFires even when ohmy is closed. Shows desktop popup + sound.")

            elif arguments.action == "schedule_shutdown":
                if not arguments.prompt:
                    return ToolResult(output="'prompt' is required for 'schedule_shutdown' action.", is_error=True)
                task = scheduler.schedule_on_shutdown(arguments.prompt)
                return ToolResult(output=f"Shutdown task scheduled.\nID: {task.id}\nType: on_shutdown (executes on OS shutdown detection)")

            elif arguments.action == "list":
                tasks = scheduler.list_tasks()
                if not tasks:
                    return ToolResult(output="No scheduled tasks.")
                lines = []
                for t in tasks:
                    line = f"{t.id} | {t.type}"
                    if t.type == "os_task":
                        line += f" | cron={t.cron_expr} | OS-level (persists after ohmy exit)"
                    line += f" | {t.status} | {t.prompt[:80]}"
                    lines.append(line)
                return ToolResult(output="\n".join(lines))

            elif arguments.action == "cancel":
                if not arguments.task_id:
                    return ToolResult(output="'task_id' is required for 'cancel' action.", is_error=True)
                if scheduler.cancel_task(arguments.task_id):
                    return ToolResult(output=f"Task {arguments.task_id} cancelled.")
                return ToolResult(output=f"Task {arguments.task_id} not found or already completed.", is_error=True)

            else:
                return ToolResult(output=f"Unknown action: {arguments.action}. Valid actions: schedule_os, schedule_shutdown, list, cancel.", is_error=True)

        except (ValueError, RuntimeError) as exc:
            return ToolResult(output=f"Schedule error: {exc}", is_error=True)
