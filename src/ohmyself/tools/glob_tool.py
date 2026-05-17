from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field

from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolResult


class GlobToolInput(BaseModel):
    pattern: str = Field(description="Glob pattern relative to the working directory", validation_alias=AliasChoices("pattern", "path"))
    root: str | None = Field(default=None, description="Optional search root")  # 如果提供了root参数，搜索将从该目录开始，否则从当前工作目录开始
    limit: int = Field(default=200, ge=1, le=5000)


class GlobTool(BaseTool):
    name = "glob"
    description = "List files matching a glob pattern."
    input_model = GlobToolInput

    def is_read_only(self, arguments: GlobToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: GlobToolInput, context: ToolExecutionContext) -> ToolResult:
        root = _resolve_path(context.cwd, arguments.root) if arguments.root else context.cwd
        root, pattern = _normalize_glob_pattern(arguments.pattern, root)
        matches = await _glob(root, pattern, limit=arguments.limit)
        if not matches:
            return ToolResult(output="(no matches)")
        return ToolResult(output="\n".join(matches))


def _resolve_path(base: Path, candidate: str | None) -> Path:
    path = Path(candidate or ".").expanduser() # expanduser()方法将路径中的~符号展开为用户的主目录路径，如果candidate是None，则默认使用当前目录（"."）
    if not path.is_absolute():
        path = base / path
    return path.resolve()


async def _glob(root: Path, pattern: str, *, limit: int) -> list[str]: # * 表示limit是一个关键字参数，必须以关键字方式传递
    rg = shutil.which("rg") # shutil.which()函数用于在系统的环境变量PATH中查找指定的可执行文件，并返回其路径，如果找不到则返回None。这里是检查系统中是否安装了ripgrep工具（rg），如果安装了就使用它来进行文件搜索，因为它比Python的glob模块更快。
    # shutil是Python的一个标准库模块，提供了一些高级的文件操作功能，比如复制、移动、删除文件和目录等。which()函数是shutil模块中的一个函数，用于查找可执行文件的路径。
    if rg and ("**" in pattern or "/" in pattern):
        process = await asyncio.create_subprocess_exec(
            rg,
            "--files",
            "--glob",
            pattern,
            ".",
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        lines: list[str] = []
        assert process.stdout is not None # 断言process.stdout不为None，告诉类型检查器这个属性在这里是可用的
        while len(lines) < limit:
            raw = await process.stdout.readline()  # 
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)
        if process.returncode is None:
            process.terminate()
            await process.wait()
        lines.sort()
        return lines
    return sorted(str(path.relative_to(root)) for path in root.glob(pattern))[:limit]


def _normalize_glob_pattern(pattern: str, root: Path) -> tuple[Path, str]:
    pp = Path(pattern)
    if not pp.is_absolute():
        return root, pattern

    parts = pp.parts
    glob_idx = None
    for i, p in enumerate(parts):
        if any(c in p for c in ('*', '?', '[')):
            glob_idx = i
            break

    if glob_idx is None:
        return pp.parent, pp.name
    if glob_idx == 0:
        return root, pattern

    return Path(*parts[:glob_idx]), str(Path(*parts[glob_idx:]))

