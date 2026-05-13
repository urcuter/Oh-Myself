"""Conversation message models."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    media_type: str
    data: str
    source_path: str = ""

    @classmethod
    # 这个类方法返回了自己
    def from_path(cls, path: str | Path) -> "ImageBlock":
        resolved = Path(path).expanduser().resolve() 
        media_type, _ = mimetypes.guess_type(str(resolved))
        if not media_type or not media_type.startswith("image/"):
            raise ValueError(f"Unsupported image attachment: {resolved}")
        payload = base64.b64encode(resolved.read_bytes()).decode("ascii") # 将图片文件内容编码为 Base64 字符串
        return cls(media_type=media_type, data=payload, source_path=str(resolved))


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str = Field(default_factory=lambda: f"toolu_{uuid4().hex}")
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = Annotated[
    TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),
]


class ConversationMessage(BaseModel):
    """
        定义了一个对话消息类，包含角色（用户或助手）和内容（文本块、图片块、工具使用块或工具结果块）。提供了从用户文本创建消息、获取纯文本内容、获取工具使用块、将消息转换为API参数格式以及检查消息是否有效的方法。
    """

    role: Literal["user", "assistant"] # 变量只能取"user"或"assistant"
    content: list[ContentBlock] = Field(default_factory=list)
    # Field(default_factory=list) 默认值是一个空列表，确保每个实例都有自己的列表，而不是共享同一个列表，每次创建对象时生成一个新的空列表
    
    @field_validator("content", mode="before") # 在验证器中，mode="before"表示在字段值被解析和验证之前调用这个方法
    @classmethod
    def _normalize_content(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        return value

    @classmethod
    def from_user_text(cls, text: str) -> "ConversationMessage":
        return cls(role="user", content=[TextBlock(text=text)])

    @property # 让方法可以向属性一样访问
    def text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [block for block in self.content if isinstance(block, ToolUseBlock)]

    def to_api_param(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": [serialize_content_block(block) for block in self.content],
        }

    def is_effectively_empty(self) -> bool:
        for block in self.content:
            if isinstance(block, TextBlock) and block.text.strip():
                return False
            if isinstance(block, (ImageBlock, ToolUseBlock, ToolResultBlock)):
                return False
        return True


def serialize_content_block(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ImageBlock):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": block.media_type,
                "data": block.data,
            },
        }
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


def assistant_message_from_api(raw_message: Any) -> ConversationMessage:
    content: list[ContentBlock] = []
    for raw_block in getattr(raw_message, "content", []):
        block_type = getattr(raw_block, "type", None)
        if block_type == "text":
            content.append(TextBlock(text=getattr(raw_block, "text", "")))
        elif block_type == "tool_use":
            content.append(
                ToolUseBlock(
                    id=getattr(raw_block, "id", f"toolu_{uuid4().hex}"),
                    name=getattr(raw_block, "name", ""),
                    input=dict(getattr(raw_block, "input", {}) or {}),
                )
            )
    return ConversationMessage(role="assistant", content=content)

