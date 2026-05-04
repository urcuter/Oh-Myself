from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ohmyself.api.usage import UsageSnapshot
from ohmyself.engine.messages import ConversationMessage, TextBlock
from ohmyself.services.session_storage import get_project_session_dir, load_latest_session_snapshot, save_session_snapshot


def test_session_snapshot_round_trip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    messages = [
        ConversationMessage.from_user_text("hello"),
        ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
    ]
    path = save_session_snapshot(
        cwd=tmp_path / "project",
        model="gpt-test",
        messages=messages,
        usage=UsageSnapshot(input_tokens=3, output_tokens=5),
        session_id="sess-123",
        session_started_at=datetime.fromisoformat("2026-05-04T10:00:00+08:00").isoformat(),
        tool_metadata={"active_profile": "openai-compatible"},
    )
    loaded = load_latest_session_snapshot(tmp_path / "project")
    assert path.exists()
    assert loaded is not None
    assert loaded["session_id"] == "sess-123"
    assert loaded["model"] == "gpt-test"
    assert loaded["usage"].output_tokens == 5
    assert len(loaded["messages"]) == 2
    assert loaded["messages"][0].text == "hello"
    assert (get_project_session_dir(tmp_path / "project") / "session-sess-123.json").exists()
