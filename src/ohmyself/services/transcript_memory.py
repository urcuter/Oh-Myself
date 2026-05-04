from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ohmyself.config.paths import get_memory_dir


@dataclass
class SessionTranscriptWriter:
    session_id: str
    cwd: str
    model: str
    started_at: datetime
    turn_index: int = 0
    _session_header_written: bool = False
    _current_turn_open: bool = False

    def reset_session(self, *, session_id: str, cwd: str, model: str, started_at: datetime) -> None:
        self.session_id = session_id
        self.cwd = cwd
        self.model = model
        self.started_at = started_at
        self.turn_index = 0
        self._session_header_written = False
        self._current_turn_open = False

    @property
    def path(self) -> Path:
        return get_memory_dir() / f"{self.started_at.date().isoformat()}.md"

    def record_user_prompt(self, prompt: str) -> None:
        self.turn_index += 1
        self._current_turn_open = True
        self._ensure_session_header()
        self._append(
            [
                f"### Turn {self.turn_index}",
                "",
                "#### User",
                "",
                "```text",
                prompt.rstrip(),
                "```",
            ]
        )

    def record_tool_started(self, tool_name: str, tool_input: dict[str, object]) -> None:
        self._ensure_turn_context()
        self._append(
            [
                f"#### Tool `{tool_name}`",
                "",
                "```json",
                json.dumps(tool_input, ensure_ascii=False, indent=2),
                "```",
            ]
        )

    def record_tool_completed(self, tool_name: str, output: str, *, is_error: bool) -> None:
        self._ensure_turn_context()
        status = "error" if is_error else "ok"
        self._append(
            [
                f"#### Tool Result `{tool_name}` [{status}]",
                "",
                "```text",
                output.rstrip() or "(no output)",
                "```",
            ]
        )

    def record_status(self, label: str, message: str) -> None:
        self._ensure_turn_context()
        self._append(
            [
                f"#### {label}",
                "",
                "```text",
                message.rstrip(),
                "```",
            ]
        )

    def record_assistant_message(self, message: str) -> None:
        if not message.strip():
            return
        self._ensure_turn_context()
        self._append(
            [
                "#### Assistant",
                "",
                "```text",
                message.rstrip(),
                "```",
            ]
        )
        self._current_turn_open = False

    def _ensure_turn_context(self) -> None:
        self._ensure_session_header()
        if self._current_turn_open:
            return
        self.turn_index += 1
        self._current_turn_open = True
        self._append([f"### Turn {self.turn_index}", "", "_continued_", ""])

    def _ensure_session_header(self) -> None:
        if self._session_header_written:
            return
        lines: list[str] = []
        if not self.path.exists() or not self.path.read_text(encoding="utf-8").strip():
            lines.extend([f"# {self.started_at.date().isoformat()}", ""])
        else:
            lines.append("")
        lines.extend(
            [
                f"## Session {self.session_id}",
                "",
                f"- Started: {self.started_at.isoformat()}",
                f"- CWD: {self.cwd}",
                f"- Model: {self.model}",
                "",
            ]
        )
        self._append(lines, raw=True)
        self._session_header_written = True

    def _append(self, lines: list[str], *, raw: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(lines)
        if not raw:
            text = "\n" + text + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(text)
