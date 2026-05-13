from __future__ import annotations

from abc import ABC, abstractmethod  # 抽象基类和抽象方法
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel


@dataclass
class ToolExecutionContext:
    cwd: Path
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):  # ABC是抽象基类，不能被实例化，强制子类必须实现抽象方法
    name: str
    description: str
    input_model: type[BaseModel]

    # BaseModel是pydantic的基类，用于定义输入数据的结构和验证，能检验工具输入是否符合规范，不合规会报错
    @abstractmethod  
    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        # arguments: 工具输入参数，必须是input_model定义的结构
        # context: 执行上下文，包含当前工作目录和其他元数据
        ...

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments  # 只消除变量名，不改变变量名指向的内存数据
        return False

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


class ToolRegistry: # 工具注册表，管理所有工具的注册和访问
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def to_api_schema(self) -> list[dict[str, Any]]:
        return [tool.to_api_schema() for tool in self._tools.values()]

