from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from ohmyself.tools.bash_tool import BashTool
from ohmyself.tools.file_edit_tool import FileEditTool
from ohmyself.tools.file_read_tool import FileReadTool
from ohmyself.tools.file_write_tool import FileWriteTool
from ohmyself.tools.glob_tool import GlobTool
from ohmyself.tools.grep_tool import GrepTool
from ohmyself.tools.todo_write_tool import TodoWriteTool
from ohmyself.tools.tool_search_tool import ToolSearchTool


def create_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        BashTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        GlobTool(),
        GrepTool(),
        TodoWriteTool(),
        ToolSearchTool(),
    ):
        registry.register(tool)
    return registry


__all__ = ["BaseTool", "ToolExecutionContext", "ToolRegistry", "ToolResult", "create_tool_registry"]
