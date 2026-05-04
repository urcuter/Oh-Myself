from __future__ import annotations

import asyncio
import ast
import shlex
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from ohmyself.tools.base import BaseTool, ToolExecutionContext, ToolResult
from ohmyself.utils.shell import create_shell_subprocess

_READ_REMAINING_OUTPUT_TIMEOUT_SECONDS = 2.0


class BashToolInput(BaseModel):
    command: str = Field(description="Shell command to execute")
    cwd: str | None = Field(default=None, description="Working directory override")
    timeout_seconds: int = Field(default=600, ge=1, le=600)


class BashTool(BaseTool):
    name = "bash"
    description = "Run a shell command in the local repository."
    input_model = BashToolInput

    def is_read_only(self, arguments: BashToolInput) -> bool:
        return _looks_like_read_only_command(arguments.command)

    async def execute(self, arguments: BashToolInput, context: ToolExecutionContext) -> ToolResult:
        cwd = Path(arguments.cwd).expanduser() if arguments.cwd else context.cwd
        preflight_error = _preflight_interactive_command(arguments.command)
        if preflight_error is not None:
            return ToolResult(output=preflight_error, is_error=True, metadata={"interactive_required": True})
        process = await create_shell_subprocess(
            arguments.command,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=arguments.timeout_seconds)
        except asyncio.TimeoutError:
            output_buffer = await _drain_available_output(process.stdout)
            process.kill()
            await process.wait()
            output_buffer.extend(await _read_remaining_output(process))
            return ToolResult(
                output=_format_timeout_output(output_buffer, command=arguments.command, timeout_seconds=arguments.timeout_seconds),
                is_error=True,
                metadata={"returncode": process.returncode, "timed_out": True},
            )
        output_buffer = await _read_remaining_output(process)
        return ToolResult(
            output=_format_output(output_buffer),
            is_error=process.returncode != 0,
            metadata={"returncode": process.returncode},
        )


async def _read_remaining_output(process: asyncio.subprocess.Process) -> bytearray:
    output_buffer = bytearray()
    if process.stdout is not None:
        try:
            remaining = await asyncio.wait_for(process.stdout.read(), timeout=_READ_REMAINING_OUTPUT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            remaining = b""
        output_buffer.extend(remaining)
    return output_buffer


async def _drain_available_output(stream: asyncio.StreamReader | None, *, read_timeout: float = 0.05) -> bytearray:
    output_buffer = bytearray()
    if stream is None:
        return output_buffer
    while True:
        try:
            chunk = await asyncio.wait_for(stream.read(65536), timeout=read_timeout)
        except asyncio.TimeoutError:
            return output_buffer
        if not chunk:
            return output_buffer
        output_buffer.extend(chunk)


def _format_output(output_buffer: bytearray) -> str:
    text = output_buffer.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    if not text:
        return "(no output)"
    if len(text) > 12000:
        return f"{text[:12000]}\n...[truncated]..."
    return text


def _format_timeout_output(output_buffer: bytearray, *, command: str, timeout_seconds: int) -> str:
    parts = [f"Command timed out after {timeout_seconds} seconds."]
    text = _format_output(output_buffer)
    if text != "(no output)":
        parts.extend(["", "Partial output:", text])
    hint = _interactive_command_hint(command=command, output=text)
    if hint:
        parts.extend(["", hint])
    return "\n".join(parts)


def _preflight_interactive_command(command: str) -> str | None:
    lowered_command = command.lower()
    if not _looks_like_interactive_scaffold(lowered_command):
        return None
    return (
        "This command appears to require interactive input before it can continue. "
        "The bash tool is non-interactive, so prefer non-interactive flags or run the scaffolding step manually."
    )


def _interactive_command_hint(*, command: str, output: str) -> str | None:
    lowered_command = command.lower()
    if _looks_like_interactive_scaffold(lowered_command) or _looks_like_prompt(output):
        return (
            "This command appears to require interactive input. "
            "Prefer non-interactive flags or run it manually in another terminal."
        )
    return None


def _looks_like_interactive_scaffold(lowered_command: str) -> bool:
    scaffold_markers: tuple[str, ...] = (
        "create-next-app",
        "npm create ",
        "pnpm create ",
        "yarn create ",
        "bun create ",
        "pnpm dlx ",
        "npm init ",
        "pnpm init ",
        "yarn init ",
        "bunx create-",
        "npx create-",
    )
    non_interactive_markers: tuple[str, ...] = (
        "--yes",
        " -y",
        "--skip-install",
        "--defaults",
        "--non-interactive",
        "--ci",
    )
    return any(marker in lowered_command for marker in scaffold_markers) and not any(marker in lowered_command for marker in non_interactive_markers)


def _looks_like_prompt(output: str) -> bool:
    if not output:
        return False
    prompt_markers: Iterable[str] = (
        "would you like",
        "ok to proceed",
        "select an option",
        "which",
        "press enter to continue",
        "?",
    )
    lowered_output = output.lower()
    return any(marker in lowered_output for marker in prompt_markers)


def _looks_like_read_only_command(command: str) -> bool:
    if any(marker in command for marker in ("&&", "||", ";", "|", ">", "<")):
        return False
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return False
    if not tokens:
        return True
    lowered = [token.lower() for token in tokens]
    head = lowered[0]
    if head in {
        "dir",
        "echo",
        "ls",
        "pwd",
        "type",
        "where",
        "which",
        "cat",
        "get-childitem",
        "get-location",
        "write-output",
    }:
        return True
    if head in {"python", "python3", "py"} and len(lowered) >= 2 and lowered[1] in {"-v", "--version"}:
        return True
    if head in {"python", "python3", "py"}:
        inline_code = _extract_python_inline_code(tokens)
        if inline_code is not None and _python_inline_code_is_read_only(inline_code):
            return True
    if head == "git" and len(lowered) >= 2 and lowered[1] in {"branch", "diff", "log", "rev-parse", "show", "status"}:
        return True
    return False


def _extract_python_inline_code(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens[:-1]):
        if token.lower() == "-c":
            return _strip_matching_quotes(tokens[index + 1])
    return None


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _python_inline_code_is_read_only(code: str) -> bool:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return False
    state: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                return False
            if not _python_expr_is_read_only(statement.value, state):
                return False
            state.add(statement.targets[0].id)
            continue
        if isinstance(statement, ast.Expr) and _python_expr_is_read_only(statement.value, state):
            continue
        return False
    return True


def _python_expr_is_read_only(node: ast.AST, assigned_names: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in assigned_names or node.id in {"True", "False", "None"}
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return all(_python_expr_is_read_only(element, assigned_names) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (key is None or _python_expr_is_read_only(key, assigned_names))
            and _python_expr_is_read_only(value, assigned_names)
            for key, value in zip(node.keys, node.values)
        )
    if isinstance(node, ast.BinOp):
        return _python_expr_is_read_only(node.left, assigned_names) and _python_expr_is_read_only(node.right, assigned_names)
    if isinstance(node, ast.UnaryOp):
        return _python_expr_is_read_only(node.operand, assigned_names)
    if isinstance(node, ast.BoolOp):
        return all(_python_expr_is_read_only(value, assigned_names) for value in node.values)
    if isinstance(node, ast.Compare):
        return _python_expr_is_read_only(node.left, assigned_names) and all(
            _python_expr_is_read_only(comparator, assigned_names) for comparator in node.comparators
        )
    if isinstance(node, ast.IfExp):
        return all(
            _python_expr_is_read_only(candidate, assigned_names)
            for candidate in (node.test, node.body, node.orelse)
        )
    if isinstance(node, ast.Subscript):
        return _python_expr_is_read_only(node.value, assigned_names) and _python_expr_is_read_only(node.slice, assigned_names)
    if isinstance(node, ast.Slice):
        return all(
            candidate is None or _python_expr_is_read_only(candidate, assigned_names)
            for candidate in (node.lower, node.upper, node.step)
        )
    if isinstance(node, ast.Call):
        return _python_call_is_read_only(node, assigned_names)
    return False


def _python_call_is_read_only(node: ast.Call, assigned_names: set[str]) -> bool:
    if node.keywords and any(keyword.arg is None for keyword in node.keywords):
        return False
    if not isinstance(node.func, ast.Name):
        return False
    if node.func.id not in {"print", "abs", "all", "any", "bool", "dict", "float", "int", "len", "list", "max", "min", "pow", "repr", "round", "set", "sorted", "str", "sum", "tuple"}:
        return False
    return all(_python_expr_is_read_only(argument, assigned_names) for argument in node.args) and all(
        _python_expr_is_read_only(keyword.value, assigned_names) for keyword in node.keywords
    )
