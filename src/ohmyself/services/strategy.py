from __future__ import annotations

from pathlib import Path

from ohmyself.config.paths import get_home_dir

STRATEGY_FILENAME = "strategy.md"


def get_strategy_path() -> Path:
    return get_home_dir() / STRATEGY_FILENAME


def read_strategy() -> str:
    path = get_strategy_path()
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def update_strategy(content: str) -> None:
    path = get_strategy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def format_strategy_for_prompt() -> str:
    content = read_strategy()
    if not content.strip():
        return "(no long-term strategy defined)"
    return content


def has_strategy_content() -> bool:
    return bool(read_strategy().strip())
