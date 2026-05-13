from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ohmyself.config.paths import get_home_dir


def _get_schedules_path() -> Path:
    return get_home_dir() / "schedules.json"


def _get_scripts_dir() -> Path:
    path = get_home_dir() / "scripts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_notification_script_path(task_id: str) -> Path:
    return _get_scripts_dir() / f"task_{task_id}.vbs"


def _now() -> datetime:
    return datetime.now().astimezone()


@dataclass
class ScheduledTask:
    id: str
    type: str  # "os_task" | "on_shutdown"
    prompt: str
    cron_expr: str | None = None  # cron expression for os_task
    created_at: str = field(default_factory=lambda: _now().isoformat())
    status: str = "pending"  # pending | executed | cancelled

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "prompt": self.prompt,
            "cron_expr": self.cron_expr,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ScheduledTask:
        return cls(
            id=d.get("id", ""),
            type=d.get("type", ""),
            prompt=d.get("prompt", ""),
            cron_expr=d.get("cron_expr"),
            created_at=d.get("created_at", ""),
            status=d.get("status", "pending"),
        )


class SchedulerService:
    def __init__(self) -> None:
        self._tasks: list[ScheduledTask] = []
        self._os_shutdown_pending = False
        self._load()

    def _load(self) -> None:
        path = _get_schedules_path()
        if not path.exists():
            self._tasks = []
            self._os_shutdown_pending = False
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                tasks_data = data.get("tasks", [])
                self._tasks = [ScheduledTask.from_dict(t) for t in tasks_data]
                self._os_shutdown_pending = data.get("_os_shutdown_pending", False)
            else:
                self._tasks = [ScheduledTask.from_dict(t) for t in data]
                self._os_shutdown_pending = False
        except (json.JSONDecodeError, TypeError):
            self._tasks = []
            self._os_shutdown_pending = False

    def _save(self) -> None:
        path = _get_schedules_path()
        data = {
            "tasks": [t.to_dict() for t in self._tasks],
            "_os_shutdown_pending": self._os_shutdown_pending,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def schedule_on_os(self, cron_expr: str, prompt: str) -> ScheduledTask:
        params, error = _cron_to_schtasks_params(cron_expr)
        if error:
            raise ValueError(error)
        task = ScheduledTask(
            id=uuid4().hex[:12],
            type="os_task",
            prompt=prompt.strip(),
            cron_expr=cron_expr.strip(),
        )
        script_path = _write_notification_script(task.id, prompt)
        try:
            _register_schtasks(task.id, params, script_path)
        except Exception:
            self._tasks.append(task)
            self._save()
            raise
        self._tasks.append(task)
        self._save()
        return task

    def schedule_on_shutdown(self, prompt: str) -> ScheduledTask:
        task = ScheduledTask(
            id=uuid4().hex[:12],
            type="on_shutdown",
            prompt=prompt.strip(),
        )
        self._tasks.append(task)
        self._save()
        return task

    def remove_os_task(self, task_id: str) -> bool:
        for task in self._tasks:
            if task.id == task_id and task.type == "os_task":
                _remove_schtasks(task.id)
                script_path = _get_notification_script_path(task.id)
                try:
                    script_path.unlink(missing_ok=True)
                except Exception:
                    pass
                task.status = "cancelled"
                self._save()
                return True
        return False

    def list_tasks(self) -> list[ScheduledTask]:
        return list(self._tasks)

    def list_os_tasks(self) -> list[ScheduledTask]:
        return [t for t in self._tasks if t.type == "os_task" and t.status != "cancelled"]

    def cancel_task(self, task_id: str) -> bool:
        for task in self._tasks:
            if task.id == task_id and task.status == "pending":
                task.status = "cancelled"
                self._save()
                return True
        return False

    def get_shutdown_tasks(self) -> list[ScheduledTask]:
        return [t for t in self._tasks if t.type == "on_shutdown" and t.status == "pending"]

    def mark_executed(self, task_id: str) -> None:
        for task in self._tasks:
            if task.id == task_id and task.status == "pending":
                task.status = "executed"
                self._save()
                return

    def set_os_shutdown_pending(self) -> None:
        self._os_shutdown_pending = True
        self._save()

    def get_os_shutdown_pending(self) -> bool:
        return self._os_shutdown_pending

    def clear_os_shutdown_pending(self) -> None:
        self._os_shutdown_pending = False
        self._save()


def _cron_to_schtasks_params(cron_expr: str) -> tuple[str | None, str | None]:
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return None, "Cron expression must have 5 fields (minute hour day_of_month month day_of_week)"

    minute, hour, day_of_month, month, day_of_week = parts

    def _valid_time(m: str, h: str) -> tuple[int, int] | None:
        try:
            mi = int(m)
            hi = int(h)
            if 0 <= mi <= 59 and 0 <= hi <= 23:
                return mi, hi
        except ValueError:
            pass
        return None

    if minute.startswith("*/") and hour == "*" and day_of_month == "*" and month == "*" and day_of_week == "*":
        interval = int(minute[2:])
        if interval < 1:
            return None, "Minute interval must be >= 1"
        return f"/SC MINUTE /MO {interval}", None

    if minute == "0" and hour.startswith("*/") and day_of_month == "*" and month == "*" and day_of_week == "*":
        interval = int(hour[2:])
        if interval < 1:
            return None, "Hour interval must be >= 1"
        return f"/SC HOURLY /MO {interval}", None

    vt = _valid_time(minute, hour)
    if vt is None:
        return None, f"Invalid time: minute={minute}, hour={hour}"

    mi, hi = vt
    time_str = f"{hi:02d}:{mi:02d}"

    if day_of_month == "*" and month == "*" and day_of_week == "*":
        return f"/SC DAILY /ST {time_str}", None

    if day_of_month == "*" and month == "*" and day_of_week != "*":
        day_map = {"0": "SUN", "1": "MON", "2": "TUE", "3": "WED", "4": "THU", "5": "FRI", "6": "SAT", "7": "SUN"}
        days: list[str] = []
        for part in day_of_week.split(","):
            part = part.strip()
            if "-" in part:
                try:
                    start_s, end_s = part.split("-", 1)
                    for i in range(int(start_s), int(end_s) + 1):
                        name = day_map.get(str(i))
                        if name and name not in days:
                            days.append(name)
                except ValueError:
                    return None, f"Invalid day range: {part}"
            else:
                name = day_map.get(part)
                if name and name not in days:
                    days.append(name)
        if days:
            return f"/SC WEEKLY /D {','.join(days)} /ST {time_str}", None
        return None, f"Cannot parse day_of_week: {day_of_week}"

    if month == "*" and day_of_week == "*":
        try:
            dom = int(day_of_month)
            if 1 <= dom <= 31:
                return f"/SC MONTHLY /D {dom} /ST {time_str}", None
        except ValueError:
            pass
        return None, f"Invalid day_of_month: {day_of_month}"

    return None, f"Cannot convert cron '{cron_expr}' to schtasks. Use Windows Task Scheduler directly."


def _write_notification_script(task_id: str, prompt: str) -> str:
    escaped = prompt.replace('"', '""')
    script_content = f'''Set oWS = CreateObject("WScript.Shell")
oWS.Popup "{escaped}", 0, "ohmyself \u5b9a\u65f6\u63d0\u9192", 64
oWS.Run "powershell -Command [System.Media.SystemSounds]::Beep.Play()", 0, False
'''
    script_path = _get_notification_script_path(task_id)
    script_path.write_text(script_content, encoding="utf-8")
    return str(script_path)


def _schtasks_name(task_id: str) -> str:
    return f"ohmyself_{task_id}"


def _register_schtasks(task_id: str, params: str, script_path: str) -> None:
    if platform.system() != "Windows":
        raise RuntimeError("OS scheduling is only supported on Windows")
    task_name = _schtasks_name(task_id)
    cmd = f'schtasks /Create /TN "{task_name}" /TR "wscript.exe //B \\"{script_path}\\"" {params} /F'
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"schtasks failed: {error_msg}")


def _remove_schtasks(task_id: str) -> None:
    if platform.system() != "Windows":
        return
    task_name = _schtasks_name(task_id)
    subprocess.run(
        f'schtasks /Delete /TN "{task_name}" /F',
        capture_output=True, text=True, shell=True,
    )
