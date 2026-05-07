from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel

from ohmyself.config.settings import PermissionSettings
from ohmyself.engine.query import QueryContext, _execute_tool_call
from ohmyself.permissions import PermissionChecker
from ohmyself.tools.base import BaseTool, ToolRegistry, ToolResult


class _WriteInput(BaseModel):
    path: str
    content: str


class _WriteTool(BaseTool):
    name = "write_file"
    description = "test write tool"
    input_model = _WriteInput

    async def execute(self, arguments: BaseModel, context) -> ToolResult:
        del context
        return ToolResult(output=f"wrote {arguments.path}")


def test_plan_auto_allow_write_file_skips_permission_prompt():
    registry = ToolRegistry()
    registry.register(_WriteTool())

    async def _unexpected_prompt(tool_name: str, reason: str) -> bool:
        raise AssertionError(f"permission prompt should not be called: {tool_name} {reason}")

    context = QueryContext(
        api_client=None,  # type: ignore[arg-type]
        tool_registry=registry,
        permission_checker=PermissionChecker(PermissionSettings(mode="default")),
        cwd=Path.cwd(),
        model="test-model",
        system_prompt="test",
        max_tokens=128,
        permission_prompt=_unexpected_prompt,
        tool_metadata={"auto_allow_write_file_for_plan": True},
    )

    result = asyncio.run(
        _execute_tool_call(
            context,
            "write_file",
            "tool-1",
            {"path": "plan.txt", "content": "hello"},
        )
    )

    assert result.block.is_error is False
    assert "wrote plan.txt" == result.block.content
