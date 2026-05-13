from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult


def create_tool_registry() -> ToolRegistry:
    from ohmyself.tools.bash_tool import BashTool
    from ohmyself.tools.file_edit_tool import FileEditTool
    from ohmyself.tools.file_read_tool import FileReadTool
    from ohmyself.tools.file_write_tool import FileWriteTool
    from ohmyself.tools.glob_tool import GlobTool
    from ohmyself.tools.grep_tool import GrepTool
    from ohmyself.tools.subagent_tool import DelegateTaskTool
    from ohmyself.tools.todo_write_tool import TodoWriteTool
    from ohmyself.tools.schedule_task_tool import ScheduleTaskTool
    from ohmyself.tools.tool_search_tool import ToolSearchTool
    from ohmyself.tools.update_user_status_tool import UpdateUserStatusTool

    registry = ToolRegistry()
    for tool in (
        BashTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        GlobTool(),
        GrepTool(),
        DelegateTaskTool(),
        TodoWriteTool(),
        ScheduleTaskTool(),
        ToolSearchTool(),
        UpdateUserStatusTool(),
    ):
        registry.register(tool)
    return registry


__all__ = ["BaseTool", "ToolExecutionContext", "ToolRegistry", "ToolResult", "create_tool_registry"]
