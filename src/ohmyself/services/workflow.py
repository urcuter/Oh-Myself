from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ohmyself.config.paths import get_home_dir

WORKFLOW_CATEGORIES = {"learning", "work", "health", "coding", "general"}
WORKFLOW_STATUSES = {"active", "archived"}


class WorkflowError(ValueError):
    pass


class WorkflowNotFoundError(WorkflowError):
    pass


@dataclass(frozen=True)
class WorkflowStep:
    order: int
    title: str
    description: str


@dataclass(frozen=True)
class Workflow:
    workflow_id: str
    name: str
    description: str
    category: str
    steps: tuple[WorkflowStep, ...]
    conditions: str
    created_at: datetime
    updated_at: datetime
    status: str
    tags: tuple[str, ...]


def get_workflow_dir() -> Path:
    path = get_home_dir() / "workflows"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_workflow_path() -> Path:
    return get_workflow_dir() / "workflows.json"


def create_workflow(
    name: str,
    *,
    description: str = "",
    category: str = "general",
    steps: list[dict[str, Any]] | None = None,
    conditions: str = "",
    tags: tuple[str, ...] = (),
    now: datetime | None = None,
) -> Workflow:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise WorkflowError("workflow name cannot be empty")
    if category not in WORKFLOW_CATEGORIES:
        raise WorkflowError(f"unknown category: {category}, must be one of {WORKFLOW_CATEGORIES}")

    created_at = now or datetime.now().astimezone()
    workflow_id = f"WF-{created_at.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"

    parsed_steps: list[WorkflowStep] = []
    if steps:
        for idx, raw in enumerate(steps, start=1):
            if isinstance(raw, dict):
                parsed_steps.append(
                    WorkflowStep(
                        order=int(raw.get("order", idx)),
                        title=str(raw.get("title", "")),
                        description=str(raw.get("desc", raw.get("description", ""))),
                    )
                )
    parsed_steps.sort(key=lambda s: s.order)

    workflow = Workflow(
        workflow_id=workflow_id,
        name=cleaned_name,
        description=description.strip(),
        category=category,
        steps=tuple(parsed_steps),
        conditions=conditions.strip(),
        created_at=created_at,
        updated_at=created_at,
        status="active",
        tags=tags,
    )

    workflows = list_workflows()
    workflows.append(workflow)
    _write_workflows(workflows)
    return workflow


def list_workflows(*, category: str | None = None, status: str | None = None) -> list[Workflow]:
    payload = _read_payload()
    items = payload.get("workflows", [])
    if not isinstance(items, list):
        return []
    workflows: list[Workflow] = []
    for item in items:
        if isinstance(item, dict):
            try:
                wf = _workflow_from_payload(item)
            except (KeyError, TypeError, ValueError):
                continue
            if category is not None and wf.category != category:
                continue
            if status is not None and wf.status != status:
                continue
            workflows.append(wf)
    return workflows


def get_workflow(workflow_id: str) -> Workflow:
    for wf in list_workflows():
        if wf.workflow_id == workflow_id:
            return wf
    raise WorkflowNotFoundError(f"workflow not found: {workflow_id}")


def update_workflow(workflow_id: str, **updates: object) -> Workflow:
    def apply(workflow: Workflow) -> Workflow:
        values = {
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "description": workflow.description,
            "category": workflow.category,
            "steps": workflow.steps,
            "conditions": workflow.conditions,
            "created_at": workflow.created_at,
            "updated_at": datetime.now().astimezone(),
            "status": workflow.status,
            "tags": workflow.tags,
        }
        for key in updates:
            if key == "category" and updates[key] not in WORKFLOW_CATEGORIES:
                raise WorkflowError(f"unknown category: {updates[key]}")
            if key == "steps" and isinstance(updates[key], list):
                parsed: list[WorkflowStep] = []
                for idx, raw in enumerate(updates[key], start=1):
                    if isinstance(raw, dict):
                        parsed.append(
                            WorkflowStep(
                                order=int(raw.get("order", idx)),
                                title=str(raw.get("title", "")),
                                description=str(raw.get("desc", raw.get("description", ""))),
                            )
                        )
                parsed.sort(key=lambda s: s.order)
                values["steps"] = tuple(parsed)
                continue
            if key in values:
                values[key] = updates[key]
        return Workflow(**values)  # type: ignore[arg-type]

    return _update_workflow(workflow_id, apply)


def delete_workflow(workflow_id: str) -> bool:
    workflows = list_workflows()
    filtered = [wf for wf in workflows if wf.workflow_id != workflow_id]
    if len(filtered) == len(workflows):
        return False
    _write_workflows(filtered)
    return True


def archive_workflow(workflow_id: str) -> Workflow:
    return update_workflow(workflow_id, status="archived")


def format_workflows_list_markdown(workflows: list[Workflow] | None = None) -> str:
    entries = workflows if workflows is not None else list_workflows()
    if not entries:
        return "没有工作流。使用 `/workflow create <名称>` 创建一个。"

    active = [wf for wf in entries if wf.status == "active"]
    archived = [wf for wf in entries if wf.status == "archived"]

    lines: list[str] = ["# 工作流"]
    if active:
        lines.append("")
        for wf in sorted(active, key=lambda w: w.created_at):
            cat_label = _category_label(wf.category)
            step_count = len(wf.steps)
            lines.append(f"- `{wf.workflow_id}` {cat_label} **{wf.name}** ({step_count}步)")
            if wf.description:
                lines.append(f"  {wf.description}")
            if wf.conditions:
                lines.append(f"  适用: {wf.conditions}")
    if archived:
        lines.append("")
        lines.append("## 已归档")
        for wf in sorted(archived, key=lambda w: w.created_at):
            lines.append(f"- `{wf.workflow_id}` ~~{wf.name}~~")
    return "\n".join(lines)


def format_workflow_detail_markdown(workflow: Workflow) -> str:
    lines = [
        f"# {workflow.name}",
        f"",
        f"**ID**: `{workflow.workflow_id}`",
        f"**分类**: {_category_label(workflow.category)}",
        f"**状态**: {workflow.status}",
        f"**创建**: {workflow.created_at.strftime('%Y-%m-%d %H:%M')}",
        f"**更新**: {workflow.updated_at.strftime('%Y-%m-%d %H:%M')}",
    ]
    if workflow.description:
        lines.append(f"")
        lines.append(f"**说明**: {workflow.description}")
    if workflow.conditions:
        lines.append(f"")
        lines.append(f"**适用条件**: {workflow.conditions}")
    if workflow.tags:
        lines.append(f"")
        lines.append(f"**标签**: {', '.join(workflow.tags)}")
    if workflow.steps:
        lines.append(f"")
        lines.append(f"## 步骤")
        for step in workflow.steps:
            lines.append(f"{step.order}. **{step.title}**")
            if step.description:
                lines.append(f"   {step.description}")
    else:
        lines.append(f"")
        lines.append(f"*暂无步骤*")
    return "\n".join(lines)


def format_workflows_for_prompt() -> str:
    """简洁摘要，注入系统提示用。"""
    workflows = list_workflows(status="active")
    if not workflows:
        return ""

    lines = [
        "## 可用工作流",
        "用户预设了以下工作流，在用户开始工作或学习时，可主动建议匹配的工作流：",
        "",
    ]
    for wf in sorted(workflows, key=lambda w: w.created_at):
        step_summary = " → ".join(s.title for s in wf.steps) if wf.steps else "无步骤"
        condition_note = f"（{wf.conditions}）" if wf.conditions else ""
        lines.append(f"- **{wf.name}** [{_category_label(wf.category)}]{condition_note}: {step_summary}")
    return "\n".join(lines)


def _update_workflow(workflow_id: str, updater) -> Workflow:
    workflows = list_workflows()
    updated: Workflow | None = None
    next_workflows: list[Workflow] = []
    for wf in workflows:
        if wf.workflow_id == workflow_id:
            updated = updater(wf)
            next_workflows.append(updated)
        else:
            next_workflows.append(wf)
    if updated is None:
        raise WorkflowNotFoundError(f"workflow not found: {workflow_id}")
    _write_workflows(next_workflows)
    return updated


def _read_payload() -> dict[str, Any]:
    path = get_workflow_path()
    if not path.exists():
        return {"workflows": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"workflows": []}


def _write_workflows(workflows: list[Workflow]) -> None:
    path = get_workflow_path()
    payload = {"workflows": [_workflow_to_payload(wf) for wf in workflows]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _workflow_to_payload(workflow: Workflow) -> dict[str, Any]:
    return {
        "workflow_id": workflow.workflow_id,
        "name": workflow.name,
        "description": workflow.description,
        "category": workflow.category,
        "steps": [
            {"order": s.order, "title": s.title, "desc": s.description}
            for s in workflow.steps
        ],
        "conditions": workflow.conditions,
        "created_at": workflow.created_at.isoformat(timespec="seconds"),
        "updated_at": workflow.updated_at.isoformat(timespec="seconds"),
        "status": workflow.status,
        "tags": list(workflow.tags),
    }


def _workflow_from_payload(payload: dict[str, Any]) -> Workflow:
    status = str(payload.get("status", "active"))
    if status not in WORKFLOW_STATUSES:
        status = "active"
    category = str(payload.get("category", "general"))
    if category not in WORKFLOW_CATEGORIES:
        category = "general"

    steps: list[WorkflowStep] = []
    for raw in payload.get("steps", []):
        if isinstance(raw, dict):
            steps.append(
                WorkflowStep(
                    order=int(raw.get("order", len(steps) + 1)),
                    title=str(raw.get("title", "")),
                    description=str(raw.get("desc", raw.get("description", ""))),
                )
            )
    steps.sort(key=lambda s: s.order)

    created_at = datetime.fromisoformat(str(payload["created_at"]))
    updated_at = datetime.fromisoformat(str(payload["updated_at"]))

    return Workflow(
        workflow_id=str(payload["workflow_id"]),
        name=str(payload["name"]),
        description=str(payload.get("description", "")),
        category=category,
        steps=tuple(steps),
        conditions=str(payload.get("conditions", "")),
        created_at=created_at,
        updated_at=updated_at,
        status=status,
        tags=tuple(str(t) for t in payload.get("tags", [])),
    )


def _category_label(category: str) -> str:
    labels = {
        "learning": "[学习]",
        "work": "[工作]",
        "health": "[健康]",
        "coding": "[编程]",
        "general": "[通用]",
    }
    return labels.get(category, f"[{category}]")
