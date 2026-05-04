"""Filesystem paths for Oh Myself."""

from __future__ import annotations

import os
from pathlib import Path


def get_home_dir() -> Path:
    configured = os.environ.get("OHMYSELF_HOME", "").strip()
    path = Path(configured).expanduser() if configured else Path.home() / ".ohmyself"
    resolved = path.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def get_settings_path() -> Path:
    return get_home_dir() / "settings.json"


def get_credentials_path() -> Path:
    return get_home_dir() / "credentials.json"


def get_data_dir() -> Path:
    path = get_home_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_sessions_dir() -> Path:
    path = get_data_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_logs_dir() -> Path:
    path = get_home_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_memory_dir() -> Path:
    path = get_home_dir() / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path
