from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolResult
from ohmyself.tools.path_utils import resolve_workspace_path


class FileEditToolInput(BaseModel):
    path: str = Field(description="Path of the file to edit")
    old_str: str = Field(description="Existing text to replace")
    new_str: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False)


class FileEditTool(BaseTool):
    name = "edit_file"
    description = "Edit an existing file by replacing a string."
    input_model = FileEditToolInput

    async def execute(self, arguments: FileEditToolInput, context: ToolExecutionContext) -> ToolResult:
        extra_roots = _resolve_extra_roots(context)
        try:
            path = resolve_workspace_path(context.cwd, arguments.path, extra_roots=extra_roots)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        if not path.exists():
            return ToolResult(output=f"File not found: {path}", is_error=True)
        original = path.read_text(encoding="utf-8")
        if arguments.old_str not in original:
            return ToolResult(output="old_str was not found in the file", is_error=True)
        if arguments.replace_all:
            updated = original.replace(arguments.old_str, arguments.new_str)
        else:
            updated = original.replace(arguments.old_str, arguments.new_str, 1)
        path.write_text(updated, encoding="utf-8")
        return ToolResult(output=f"Updated {path}")


def _resolve_extra_roots(context: ToolExecutionContext) -> list[Path] | None:
    linked_dir = context.metadata.get("linked_dir")
    if not linked_dir or not isinstance(linked_dir, str):
        return None
    return [Path(linked_dir).expanduser().resolve()]