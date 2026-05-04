from __future__ import annotations

import asyncio
from pathlib import Path

from typer.testing import CliRunner

from ohmyself.api.client import ApiMessageCompleteEvent
from ohmyself.api.usage import UsageSnapshot
from ohmyself.cli import app
from ohmyself.config import load_settings
from ohmyself.engine.messages import ConversationMessage, TextBlock
from ohmyself.runtime import build_runtime
from ohmyself.tools import create_tool_registry


class _StaticApiClient:
    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ready")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


def test_tools_command_lists_expected_tools():
    result = CliRunner().invoke(app, ["tools"])
    assert result.exit_code == 0
    assert "read_file" in result.stdout
    assert "tool_search" in result.stdout


def test_default_settings_use_standalone_profiles(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    settings = load_settings()
    profile_name, profile = settings.resolve_profile()
    assert profile_name == "openai-compatible"
    assert profile.provider == "openai"


def test_tool_registry_is_reduced():
    names = {tool.name for tool in create_tool_registry().list_tools()}
    assert {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "todo_write", "tool_search"} <= names


def test_runtime_builds_without_openharness(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    async def _build():
        runtime = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient(), permission_mode="full_auto")
        names = {tool.name for tool in runtime.engine.tool_metadata["tool_registry"].list_tools()}
        assert "read_file" in names
        assert "skill" not in names
        assert runtime.session_id == runtime.engine.tool_metadata["session_id"]
        previous_id = runtime.session_id
        runtime.start_new_session()
        assert runtime.session_id != previous_id
        assert runtime.engine.tool_metadata["session_id"] == runtime.session_id

    asyncio.run(_build())
