from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ohmyself.services.transcript_memory import SessionTranscriptWriter


def test_transcript_writer_appends_sessions_by_date(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    started_at = datetime.fromisoformat("2026-05-04T09:30:00+08:00")
    writer = SessionTranscriptWriter(
        session_id="sess-001",
        cwd=str(tmp_path / "project"),
        model="gpt-test",
        started_at=started_at,
    )

    writer.record_user_prompt("first question")
    writer.record_tool_started("bash", {"command": "python -c \"print(10 ** 2)\""})
    writer.record_tool_completed("bash", "100", is_error=False)
    writer.record_assistant_message("10 的平方结果是 100。")

    writer.reset_session(
        session_id="sess-002",
        cwd=str(tmp_path / "project"),
        model="gpt-test",
        started_at=started_at,
    )
    writer.record_user_prompt("second question")
    writer.record_assistant_message("second answer")

    transcript_path = tmp_path / "home" / "memory" / "2026-05-04.md"
    content = transcript_path.read_text(encoding="utf-8")
    assert "# 2026-05-04" in content
    assert "## Session sess-001" in content
    assert "## Session sess-002" in content
    assert "### Turn 1" in content
    assert "Tool `bash`" in content
    assert "10 的平方结果是 100。" in content
