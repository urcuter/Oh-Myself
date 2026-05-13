from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ohmyself.config.paths import get_home_dir

DEFAULT_STATUS_FIELDS = ["睡眠情况", "身体情况", "情感状况", "学习兴趣"]


def get_status_dir() -> Path:
    path = get_home_dir() / "status"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_fields_path() -> Path:
    return get_status_dir() / "fields.json"


def get_status_path(for_date: date | None = None) -> Path:
    target = for_date or date.today()
    return get_status_dir() / f"{target.isoformat()}.json"


def get_status_fields() -> list[str]:
    path = get_fields_path()
    if not path.exists():
        return list(DEFAULT_STATUS_FIELDS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and all(isinstance(item, str) for item in data):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return list(DEFAULT_STATUS_FIELDS)


def save_status_fields(fields: list[str]) -> None:
    path = get_fields_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class StatusEntry:
    date: str
    fields: dict[str, str]
    future_expectation: str
    risks: list[str]
    preparations: list[str]
    notes: str
    updated_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "fields": self.fields,
            "future_expectation": self.future_expectation,
            "risks": self.risks,
            "preparations": self.preparations,
            "notes": self.notes,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> StatusEntry:
        return cls(
            date=str(payload.get("date", "")),
            fields=payload.get("fields") or {},
            future_expectation=str(payload.get("future_expectation", "")),
            risks=payload.get("risks") or [],
            preparations=payload.get("preparations") or [],
            notes=str(payload.get("notes", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


def get_today_status() -> StatusEntry | None:
    path = get_status_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return StatusEntry.from_payload(payload)
    except (json.JSONDecodeError, OSError):
        return None


def save_status(entry: StatusEntry) -> None:
    path = get_status_path(date.fromisoformat(entry.date))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entry.to_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_status_for_date(d: date) -> StatusEntry | None:
    path = get_status_path(d)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return StatusEntry.from_payload(payload)
    except (json.JSONDecodeError, OSError):
        return None


def get_recent_status(days: int = 7) -> list[StatusEntry]:
    entries: list[StatusEntry] = []
    for i in range(days):
        d = date.today() - timedelta(days=i)
        entry = get_status_for_date(d)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda e: e.date)


def has_today_status() -> bool:
    return get_status_path().exists()


def _format_status_markdown(entry: StatusEntry, fields: list[str]) -> str:
    lines = [f"# 个人状态 - {entry.date}"]
    lines.append("")
    for field in fields:
        value = entry.fields.get(field, "未记录")
        lines.append(f"- **{field}**：{value}")
    if entry.future_expectation:
        lines.append("")
        lines.append("## 未来预期")
        lines.append(entry.future_expectation)
    if entry.risks:
        lines.append("")
        lines.append("## 潜在风险")
        for r in entry.risks:
            lines.append(f"- {r}")
    if entry.preparations:
        lines.append("")
        lines.append("## 应对准备")
        for p in entry.preparations:
            lines.append(f"- {p}")
    if entry.notes:
        lines.append("")
        lines.append("## 备注")
        lines.append(entry.notes)
    return "\n".join(lines)


def format_today_status_markdown() -> str:
    entry = get_today_status()
    fields = get_status_fields()
    if entry is None:
        today = date.today().isoformat()
        fields_display = "、".join(fields)
        return f"# 个人状态 - {today}\n\n今日状态尚未记录。请描述你的状态（包含：{fields_display}、未来预期等）。"
    return _format_status_markdown(entry, fields)


def format_recent_status_for_prompt(days: int = 7) -> str:
    entries = get_recent_status(days)
    fields = get_status_fields()
    if not entries:
        return "(no status recorded)"
    lines: list[str] = [f"## Recent Personal Status (last {days} days)"]
    for entry in entries:
        parts = [entry.date]
        for field in fields:
            val = entry.fields.get(field, "")
            if val:
                parts.append(f"{field}: {val}")
        lines.append("- " + " | ".join(parts))
        if entry.future_expectation:
            lines.append(f"  预期: {entry.future_expectation[:200]}")
    return "\n".join(lines)


def format_recent_status_table(days: int = 7) -> str:
    entries = get_recent_status(days)
    fields = get_status_fields()
    if not entries:
        return "(无记录)"

    header = "| 日期 | " + " | ".join(fields) + " |"
    sep = "|------" + "|------" * len(fields) + "|"
    rows = [header, sep]
    for entry in entries:
        vals = [entry.fields.get(f, "-") for f in fields]
        rows.append("| " + entry.date + " | " + " | ".join(vals) + " |")
    return "\n".join(rows)
