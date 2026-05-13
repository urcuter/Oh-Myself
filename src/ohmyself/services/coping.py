from __future__ import annotations

from pathlib import Path

from ohmyself.config.paths import get_home_dir

COPING_FILENAME = "coping.md"


def get_coping_path() -> Path:
    return get_home_dir() / COPING_FILENAME


def read_coping() -> str:
    path = get_coping_path()
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def append_coping_rule(rule: str) -> str:
    path = get_coping_path()
    existing = read_coping()
    if existing.strip():
        content = existing.rstrip() + "\n" + rule.strip() + "\n"
    else:
        content = "# 应对策略\n\n" + rule.strip() + "\n"
    path.write_text(content, encoding="utf-8")
    return content


def format_coping_for_prompt() -> str:
    content = read_coping()
    if not content.strip():
        return "(no coping strategies defined)"
    return content


def has_coping_content() -> bool:
    return bool(read_coping().strip())
