from __future__ import annotations

from pydantic import BaseModel, Field

from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolResult
from ohmyself.tools.path_utils import resolve_workspace_path


class FileWriteToolInput(BaseModel):
    path: str = Field(description="Path of the file to write")
    content: str = Field(description="Full file contents")
    create_directories: bool = Field(default=True)


class FileWriteTool(BaseTool):
    name = "write_file"
    description = "Create or overwrite a text file in the local repository."
    input_model = FileWriteToolInput

    async def execute(self, arguments: FileWriteToolInput, context: ToolExecutionContext) -> ToolResult:
        try:
            path = resolve_workspace_path(context.cwd, arguments.path)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        if arguments.create_directories:
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments.content, encoding="utf-8")
        return ToolResult(output=f"Wrote {path}")
