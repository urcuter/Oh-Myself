"""Credential storage for Oh Myself."""

from __future__ import annotations

import json
from pathlib import Path

from ohmyself.config.paths import get_credentials_path


def _load_store(path: Path | None = None) -> dict[str, dict[str, str]]:
    creds_path = path or get_credentials_path()
    if not creds_path.exists():
        return {}
    try:
        data = json.loads(creds_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, dict):
            normalized[key] = {
                str(inner_key): str(inner_value)
                for inner_key, inner_value in value.items()
                if isinstance(inner_key, str)
            }
    return normalized


def _save_store(data: dict[str, dict[str, str]], path: Path | None = None) -> None:
    creds_path = path or get_credentials_path()
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def store_api_key(slot: str, api_key: str) -> None:
    data = _load_store()
    data.setdefault(slot, {})["api_key"] = api_key
    _save_store(data)


def load_api_key(slot: str) -> str | None:
    return _load_store().get(slot, {}).get("api_key")


def clear_slot(slot: str) -> None:
    data = _load_store()
    if slot in data:
        del data[slot]
        _save_store(data)

