from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field

from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolResult


class GlobToolInput(BaseModel):
    pattern: str = Field(description="Glob pattern relative to the working directory", validation_alias=AliasChoices("pattern", "path"))
    root: str | None = Field(default=None, description="Optional search root")
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
        matches = await _glob(root, arguments.pattern, limit=arguments.limit)
        if not matches:
            return ToolResult(output="(no matches)")
        return ToolResult(output="\n".join(matches))


def _resolve_path(base: Path, candidate: str | None) -> Path:
    path = Path(candidate or ".").expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


async def _glob(root: Path, pattern: str, *, limit: int) -> list[str]:
    rg = shutil.which("rg")
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
        assert process.stdout is not None
        while len(lines) < limit:
            raw = await process.stdout.readline()
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

