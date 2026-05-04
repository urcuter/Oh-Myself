from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


def resolve_shell_command(command: str) -> list[str]:
    if os.name == "nt":
        for executable in (shutil.which("pwsh"), shutil.which("powershell")):
            if executable and _shell_is_available(executable, "-NoLogo", "-NoProfile", "-Command", "exit 0"):
                return [executable, "-NoLogo", "-NoProfile", "-Command", command]
        cmd = shutil.which("cmd.exe") or "cmd.exe"
        if _shell_is_available(cmd, "/d", "/s", "/c", "exit 0"):
            return [cmd, "/d", "/s", "/c", command]
        bash = shutil.which("bash")
        if bash and _shell_is_available(bash, "-lc", "true"):
            return [bash, "-lc", command]
        return [shutil.which("cmd.exe") or "cmd.exe", "/d", "/s", "/c", command]
    bash = shutil.which("bash")
    if bash and _shell_is_available(bash, "-lc", "true"):
        return [bash, "-lc", command]
    shell = shutil.which("sh") or os.environ.get("SHELL") or "/bin/sh"
    return [shell, "-lc", command]


@lru_cache(maxsize=None)
def _shell_is_available(executable: str, *probe_args: str) -> bool:
    try:
        completed = subprocess.run(
            [executable, *probe_args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


async def create_shell_subprocess(
    command: str,
    *,
    cwd: str | Path,
    stdin: int | None = asyncio.subprocess.DEVNULL,
    stdout: int | None = None,
    stderr: int | None = None,
) -> asyncio.subprocess.Process:
    argv = resolve_shell_command(command)
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(Path(cwd).resolve()),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
