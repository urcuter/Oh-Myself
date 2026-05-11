from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from ohmyself.api.client import ApiMessageCompleteEvent, ApiTextDeltaEvent
from ohmyself.api.usage import UsageSnapshot
from ohmyself.cli import (
    _handle_connect_command,
    _handle_model_command,
    _handle_plan_command,
    _handle_restore_command,
    _perform_goal_exit,
    _perform_goal_switch,
    _stream_prompt_with_ui,
    app,
    run_repl,
)
from ohmyself.config import load_settings
from ohmyself.engine.messages import ConversationMessage, TextBlock
from ohmyself.runtime import build_runtime
from ohmyself.services import append_goal
from ohmyself.services.goal_agent import GoalAgentContext
from ohmyself.services import SessionTranscriptWriter
from ohmyself.services.plan import PlanEntry
from ohmyself.services.session_storage import save_session_snapshot
from ohmyself.tools import create_tool_registry


class _StaticApiClient:
    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ready")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class _ChunkedApiClient:
    async def stream_message(self, request):
        del request
        for chunk in ("hello ", "world"):
            yield ApiTextDeltaEvent(text=chunk)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="hello world")]),
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
    assert {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "delegate_task", "todo_write", "tool_search"} <= names


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


class _TranscriptStub:
    def __init__(self) -> None:
        self.resets: list[tuple[str, str, str]] = []
        self.statuses: list[tuple[str, str]] = []

    def reset_session(self, *, session_id: str, cwd: str, model: str, started_at: datetime) -> None:
        del started_at
        self.resets.append((session_id, cwd, model))

    def record_status(self, category: str, message: str) -> None:
        self.statuses.append((category, message))


def test_restore_command_restores_latest_workspace_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    async def _build_and_restore():
        runtime = await build_runtime(cwd=str(tmp_path / "project"), api_client=_StaticApiClient(), permission_mode="full_auto")
        save_session_snapshot(
            cwd=runtime.cwd,
            model=runtime.engine.model,
            messages=[
                ConversationMessage.from_user_text("hello"),
                ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
            ],
            usage=UsageSnapshot(input_tokens=3, output_tokens=5),
            session_id="sess-restore",
            session_started_at=datetime.fromisoformat("2026-05-04T10:00:00+08:00").isoformat(),
            tool_metadata=runtime.engine.tool_metadata,
        )
        transcript = _TranscriptStub()
        messages_before = runtime.engine.messages
        assert messages_before == []

        _handle_restore_command(runtime, transcript)

        assert [message.text for message in runtime.engine.messages] == ["hello", "world"]
        assert runtime.session_id == "sess-restore"
        assert transcript.resets == [("sess-restore", runtime.cwd, runtime.current_model())]
        assert transcript.statuses[-1] == ("Session", "Restored session sess-restore with 2 messages.")

    asyncio.run(_build_and_restore())


def test_run_repl_does_not_auto_restore(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    class _FakeSettings:
        class _Permission:
            mode = "full_auto"

        permission = _Permission()

        def resolve_profile(self, active_profile=None):
            del active_profile
            return "openai-compatible", type("Profile", (), {"label": "OpenAI Compatible"})()

    class _FakeRuntime:
        def __init__(self) -> None:
            self.cwd = str(tmp_path)
            self.session_id = "sess-new"
            self.session_started_at = datetime.now().astimezone()
            self.settings_overrides = {}

        def current_settings(self):
            return _FakeSettings()

        def current_model(self) -> str:
            return "gpt-test"

    async def _fake_build_runtime(**kwargs):
        del kwargs
        return _FakeRuntime()

    called = False

    def _unexpected_restore(runtime):
        del runtime
        nonlocal called
        called = True
        raise AssertionError("_restore_latest_session should not be called on startup")

    monkeypatch.setattr("ohmyself.cli.build_runtime", _fake_build_runtime)
    monkeypatch.setattr("ohmyself.cli._restore_latest_session", _unexpected_restore)
    monkeypatch.setattr("ohmyself.cli._build_transcript_writer", lambda runtime: _TranscriptStub())
    monkeypatch.setattr("ohmyself.cli.print_welcome", lambda **kwargs: None)
    monkeypatch.setattr("ohmyself.cli.prompt_input", lambda **kwargs: "/exit")

    asyncio.run(
        run_repl(
            cwd=str(tmp_path),
            model=None,
            max_turns=None,
            base_url=None,
            system_prompt=None,
            api_key=None,
            api_format=None,
            permission_mode=None,
            active_profile=None,
        )
    )

    assert called is False


def test_stream_prompt_without_live_does_not_duplicate_completed_message(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("ohmyself.cli.supports_live_markdown", lambda: False)
    monkeypatch.setattr("ohmyself.cli._save_runtime_snapshot", lambda runtime: None)

    async def _run():
        runtime = await build_runtime(cwd=str(tmp_path), api_client=_ChunkedApiClient(), permission_mode="full_auto")
        transcript = SessionTranscriptWriter(
            session_id=runtime.session_id,
            cwd=runtime.cwd,
            model=runtime.current_model(),
            started_at=runtime.session_started_at,
        )
        await _stream_prompt_with_ui(runtime, "say hello", transcript, decorated=True)

    asyncio.run(_run())

    output = capsys.readouterr().out

    assert output.count("hello world") == 1


def test_plan_content_temporarily_auto_allows_write_file(monkeypatch):
    seen_flags: list[object] = []

    async def _fake_stream(runtime, prompt, transcript):
        del prompt, transcript
        seen_flags.append(runtime.engine.tool_metadata.get("auto_allow_write_file_for_plan"))

    monkeypatch.setattr("ohmyself.cli.append_plan", lambda content: PlanEntry("PLAN-1", Path("plan.md"), content, datetime.now().astimezone()))
    monkeypatch.setattr("ohmyself.cli._build_plan_prompt", lambda: "organize plan")
    monkeypatch.setattr("ohmyself.cli._stream_prompt", _fake_stream)
    monkeypatch.setattr("ohmyself.cli.read_today_plan", lambda: ("# Daily Plan", Path("plan.md")))
    monkeypatch.setattr("ohmyself.cli.print_context_snapshot", lambda *args, **kwargs: None)

    runtime = SimpleNamespace(engine=SimpleNamespace(tool_metadata={}), active_goal_id=None)
    transcript = _TranscriptStub()

    asyncio.run(_handle_plan_command(runtime, transcript, "finish report"))

    assert seen_flags == [True]
    assert "auto_allow_write_file_for_plan" not in runtime.engine.tool_metadata


def test_model_command_shows_current_and_default(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    async def _run():
        runtime = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient(), permission_mode="full_auto")
        _handle_model_command(runtime, "")

    asyncio.run(_run())
    output = capsys.readouterr().out
    assert "profile=openai-compatible" in output
    assert "current=gpt-5.4" in output
    assert "default=gpt-5.4" in output


def test_model_command_switches_runtime_and_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    async def _run():
        runtime = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient(), permission_mode="full_auto")
        _handle_model_command(runtime, "gpt-5.5")
        assert runtime.current_model() == "gpt-5.5"
        settings = load_settings()
        profile_name, profile = settings.resolve_profile()
        assert profile_name == "openai-compatible"
        assert profile.last_model == "gpt-5.5"
        assert profile.default_model == "gpt-5.5"

    asyncio.run(_run())


def test_connect_command_requires_api_format(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    async def _run():
        runtime = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient(), permission_mode="full_auto")
        _handle_connect_command(runtime, "--name p1 --base-url https://x.test --api-key sk-1 --model gpt-x")

    asyncio.run(_run())
    output = capsys.readouterr().out
    assert "missing required option: --api-format" in output


def test_connect_command_creates_profile_and_switches_runtime(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))

    async def _run():
        runtime = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient(), permission_mode="full_auto")
        _handle_connect_command(
            runtime,
            "--name custom1 --base-url https://api.example.com/v1 --api-key sk-test --api-format openai --model gpt-5.5-mini",
        )
        settings = load_settings()
        profile_name, profile = settings.resolve_profile()
        assert profile_name == "custom1"
        assert profile.api_format == "openai"
        assert profile.base_url == "https://api.example.com/v1"
        assert profile.resolved_model == "gpt-5.5-mini"
        assert runtime.settings_overrides.get("active_profile") == "custom1"
        assert runtime.current_model() == "gpt-5.5-mini"
        assert runtime.engine.tool_metadata.get("active_profile") == "custom1"
        assert runtime.engine.api_client.__class__.__name__ == "OpenAICompatibleClient"

    asyncio.run(_run())


def test_goal_switch_updates_runtime_cwd_and_exit_restores(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    linked = workspace / "goal-a"
    linked.mkdir()

    async def _run():
        runtime = await build_runtime(cwd=str(workspace), api_client=_StaticApiClient(), permission_mode="full_auto")
        runtime.goal_context = GoalAgentContext()
        goal = append_goal("Goal A", linked_dir=str(linked))
        transcript = _TranscriptStub()

        _perform_goal_switch(runtime, transcript, goal.entry_id)

        assert runtime.base_cwd == str(workspace.resolve())
        assert runtime.cwd == str(linked.resolve())
        assert runtime.linked_dir == str(linked.resolve())
        assert runtime.engine.tool_metadata["linked_dir"] == str(linked.resolve())
        assert transcript.resets[-1] == (runtime.session_id, str(linked.resolve()), runtime.current_model())

        await _perform_goal_exit(runtime, transcript)

        assert runtime.cwd == str(workspace.resolve())
        assert runtime.linked_dir is None
        assert runtime.engine.tool_metadata["linked_dir"] is None
        assert transcript.resets[-1] == (runtime.session_id, str(workspace.resolve()), runtime.current_model())

    asyncio.run(_run())


def test_goal_switch_with_missing_linked_dir_stays_in_base_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OHMYSELF_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    missing = workspace / "missing-goal-dir"

    async def _run():
        runtime = await build_runtime(cwd=str(workspace), api_client=_StaticApiClient(), permission_mode="full_auto")
        runtime.goal_context = GoalAgentContext()
        goal = append_goal("Goal A", linked_dir=str(missing))
        transcript = _TranscriptStub()

        _perform_goal_switch(runtime, transcript, goal.entry_id)

        assert runtime.cwd == str(workspace.resolve())
        assert runtime.linked_dir is None
        assert runtime.engine.tool_metadata["linked_dir"] is None

    asyncio.run(_run())
