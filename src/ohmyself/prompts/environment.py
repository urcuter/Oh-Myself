from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class EnvironmentInfo:
    os_name: str
    os_version: str
    platform_machine: str
    shell: str
    cwd: str
    date: str
    python_version: str
    python_executable: str
    virtual_env: str = ""
    is_git_repo: bool = False
    git_branch: str = ""


def get_environment_info(*, cwd: str | None = None) -> EnvironmentInfo:
    resolved_cwd = str(Path(cwd or Path.cwd()).resolve())
    shell = os.environ.get("SHELL") or os.environ.get("COMSPEC") or "unknown"
    virtual_env = os.environ.get("VIRTUAL_ENV", "")
    is_git_repo = (Path(resolved_cwd) / ".git").exists()
    git_branch = ""
    if is_git_repo and shutil.which("git"):
        try:
            git_branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=resolved_cwd,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            git_branch = ""
    return EnvironmentInfo(
        os_name=platform.system(),
        os_version=platform.version(),
        platform_machine=platform.machine(),
        shell=shell,
        cwd=resolved_cwd,
        date=datetime.now(timezone.utc).astimezone().isoformat(),
        python_version=sys.version.split()[0],
        python_executable=sys.executable,
        virtual_env=virtual_env,
        is_git_repo=is_git_repo,
        git_branch=git_branch,
    )

