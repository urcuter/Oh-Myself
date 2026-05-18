from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolResult
from ohmyself.services.status import (
    get_status_fields,
    get_today_status,
    save_status,
    StatusEntry,
)
from ohmyself.services.coping import read_coping


class UpdateUserStatusInput(BaseModel):
    fields: dict[str, str] = Field(
        default_factory=dict,
        description="Status fields to update, e.g. {'身体情况': '精力充沛', '情感状况': '有点焦虑'}",
    )
    future_expectation: str = Field(
        default="",
        description="User's expectation for the near future",
    )
    notes: str = Field(
        default="",
        description="Additional notes or context",
    )


def _match_coping_rules_sync(status_entry: StatusEntry) -> list[str]:
    """Match coping rules against status entry locally (keyword-based fallback).
    
    This is a fast local match to include in the tool output.
    The AI can still do deeper semantic matching in conversation.
    """
    coping_content = read_coping()
    if not coping_content.strip():
        return []

    # Build a searchable text from the status
    status_text = " ".join(status_entry.fields.values())
    if status_entry.future_expectation:
        status_text += " " + status_entry.future_expectation

    matched: list[str] = []
    for line in coping_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Simple check: if any status field value appears in the rule condition
        for value in status_entry.fields.values():
            if value and len(value) >= 2 and value in line:
                matched.append(line)
                break

    return matched


class UpdateUserStatusTool(BaseTool):
    name = "update_user_status"
    description = (
        "Update the user's personal status for today. "
        "Use this when the user describes their current state (sleep, physical, emotional, learning interest, etc.). "
        "The tool saves the status and checks coping strategies. "
        "Field names should be in Chinese, e.g., 睡眠情况, 身体情况, 情感状况, 学习兴趣."
    )
    input_model = UpdateUserStatusInput

    async def execute(
        self, arguments: UpdateUserStatusInput, context: ToolExecutionContext
    ) -> ToolResult:
        today = date.today()
        available_fields = get_status_fields()

        existing = get_today_status()

        if existing:
            merged_fields = dict(existing.fields)
            merged_fields.update(arguments.fields)
            future_expectation = arguments.future_expectation or existing.future_expectation
            existing_risks = list(existing.risks)
            existing_preparations = list(existing.preparations)
            notes = arguments.notes or existing.notes
        else:
            merged_fields = {}
            for field in available_fields:
                merged_fields[field] = arguments.fields.get(field, "未提及")
            # Also include any extra fields the user provided
            for key, value in arguments.fields.items():
                if key not in merged_fields:
                    merged_fields[key] = value
            future_expectation = arguments.future_expectation
            existing_risks = []
            existing_preparations = []
            notes = arguments.notes

        entry = StatusEntry(
            date=today.isoformat(),
            fields=merged_fields,
            future_expectation=future_expectation,
            risks=existing_risks,
            preparations=existing_preparations,
            notes=notes,
            daily_context=existing.daily_context if existing else "",
            updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

        save_status(entry)

        # Match coping rules
        coping_matches = _match_coping_rules_sync(entry)

        # Build response
        lines: list[str] = [
            f"Status updated for {today.isoformat()}.",
            "",
            "Current status:",
        ]
        for field in available_fields:
            val = merged_fields.get(field, "未提及")
            lines.append(f"  - {field}: {val}")
        # Show any extra user-provided fields
        for key, value in merged_fields.items():
            if key not in available_fields:
                lines.append(f"  - {key}: {value}")
        if future_expectation:
            lines.append(f"  - 未来预期: {future_expectation}")
        if notes:
            lines.append(f"  - 备注: {notes}")

        if coping_matches:
            lines.append("")
            lines.append("Relevant coping strategies:")
            for rule in coping_matches:
                lines.append(f"  {rule}")

        return ToolResult(output="\n".join(lines))

    def is_read_only(self, arguments: UpdateUserStatusInput) -> bool:
        del arguments
        return False
