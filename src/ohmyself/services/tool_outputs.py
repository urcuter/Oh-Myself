from __future__ import annotations

import os


def _read_positive_int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def tool_output_inline_chars() -> int:
    return _read_positive_int_env("OHMY_TOOL_OUTPUT_INLINE_CHARS", 16000, minimum=256)


def tool_output_preview_chars() -> int:
    return _read_positive_int_env("OHMY_TOOL_OUTPUT_PREVIEW_CHARS", 3000, minimum=128)

