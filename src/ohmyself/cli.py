from __future__ import annotations

import asyncio
import ctypes
import json
import platform
import shlex
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import typer

from ohmyself import __version__
from ohmyself.auth import AuthManager
from ohmyself.config import ProviderProfile, get_home_dir, get_memory_dir, get_settings_path, load_settings
from ohmyself.engine.query import MaxTurnsExceeded
from ohmyself.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    SubagentCompleted,
    SubagentStarted,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from ohmyself.permissions import PermissionMode
from ohmyself.runtime import OhMyRuntime, build_api_client, build_runtime
from ohmyself.services import (
    MAX_ACTIVE_GOALS,
    SchedulerService,
    ScheduledTask,
    SessionTranscriptWriter,
    append_experience,
    append_goal,
    append_plan,
    build_experience_answer_prompt,
    build_experience_organize_prompt,
    build_experience_retrieval_task,
    build_plan_organize_prompt,
    complete_goal,
    format_goals_markdown,
    generate_user_profile,
    get_experience_dir,
    has_experience_content,
    has_plan_content,
    has_plan_inbox_content,
    list_goals,
    list_project_sessions,
    load_latest_session_snapshot,
    load_session_snapshot_by_id,
    read_plan_inbox,
    read_today_plan,
    save_session_snapshot,
    save_user_profile,
    stop_goal,
    update_goal_progress,
)
from ohmyself.services.goal_agent import GoalAgentContext
from ohmyself.services.goal_memory import (
    read_goal_memory,
    append_goal_memory,
    update_goal_memory_via_ai,
    AI_NOTES_FILENAME,
    USER_PREFS_FILENAME,
    CONTEXT_FILENAME,
)
from ohmyself.services.goal_memory_retriever import build_goal_memory_retrieval_task
from ohmyself.services.goal_progress import (
    assess_daily_goal_progress,
    get_last_progress_check_date,
    set_last_progress_check_date,
)
from ohmyself.services.goal_session import list_goal_sessions
from ohmyself.services.status import (
    StatusEntry,
    format_recent_status_for_prompt,
    format_recent_status_table,
    format_today_status_markdown,
    get_recent_status,
    get_status_fields,
    get_today_status,
    has_today_status,
    save_status,
    save_status_fields,
)
from ohmyself.services.coping import (
    append_coping_rule,
    format_coping_for_prompt,
    has_coping_content,
    read_coping,
)
from ohmyself.services.strategy import (
    format_strategy_for_prompt,
    has_strategy_content,
    read_strategy,
    update_strategy,
)
from ohmyself.terminal_ui import (
    GOAL_CYCLE_SENTINEL,
    RestoredSessionSummary,
    console,
    format_assistant_chunk,
    make_live_markdown,
    print_context_snapshot,
    print_error,
    print_goal_switch_feedback,
    print_help_panel,
    print_markdown,
    print_model_panel,
    print_status,
    print_status_panel,
    print_subagent_completed,
    print_subagent_started,
    print_success,
    print_tool_completed,
    print_tool_started,
    print_tools_panel,
    print_welcome,
    prompt_goal_memory_update,
    prompt_input,
    prompt_permission,
    supports_live_markdown,
    update_live_markdown,
)
from ohmyself.tools import create_tool_registry
from ohmyself.tools.base import ToolExecutionContext, ToolRegistry
from ohmyself.tools.subagent_tool import DelegateTaskTool, DelegateTaskToolInput

app = typer.Typer(name="ohmy", help="Oh Myself: a standalone terminal AI agent.", add_completion=False)
auth_app = typer.Typer(name="auth", help="Manage API keys for Oh Myself.")
provider_app = typer.Typer(name="provider", help="Manage provider profiles.")
app.add_typer(auth_app, name="auth")
app.add_typer(provider_app, name="provider")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ohmy {__version__}")
        raise typer.Exit()


def _tool_preview(arguments: dict[str, object]) -> str:
    rendered = json.dumps(arguments, ensure_ascii=False)
    return rendered if len(rendered) <= 100 else rendered[:97] + "..."


async def _permission_prompt(tool_name: str, reason: str) -> bool:
    def _ask() -> bool:
        return prompt_permission(tool_name, reason)

    return await asyncio.to_thread(_ask)


def _register_os_shutdown_handler(scheduler: SchedulerService) -> None:
    system = platform.system()
    try:
        if system == "Windows":
            CTRL_SHUTDOWN_EVENT = 5
            CTRL_LOGOFF_EVENT = 6
            handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)

            @handler_type
            def _handler(ctrl_type: int) -> bool:
                if ctrl_type in (CTRL_SHUTDOWN_EVENT, CTRL_LOGOFF_EVENT):
                    scheduler.set_os_shutdown_pending()
                    return True
                return False

            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler, True)
        else:
            import signal

            def _sig_handler(signum: int, frame: object) -> None:
                del signum, frame
                scheduler.set_os_shutdown_pending()

            signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:
        pass


def _build_transcript_writer(runtime: OhMyRuntime) -> SessionTranscriptWriter:
    return SessionTranscriptWriter(
        session_id=runtime.session_id,
        cwd=runtime.cwd,
        model=runtime.current_model(),
        started_at=runtime.session_started_at,
    )


def _sync_transcript_writer(runtime: OhMyRuntime, transcript: SessionTranscriptWriter) -> None:
    transcript.reset_session(
        session_id=runtime.session_id,
        cwd=runtime.cwd,
        model=runtime.current_model(),
        started_at=runtime.session_started_at,
    )


def _save_runtime_snapshot(runtime: OhMyRuntime) -> None:
    tool_metadata = dict(runtime.engine.tool_metadata)
    tool_metadata["active_goal_id"] = runtime.active_goal_id
    save_session_snapshot(
        cwd=runtime.cwd,
        model=runtime.engine.model,
        messages=runtime.engine.messages,
        usage=runtime.engine.total_usage,
        session_id=runtime.session_id,
        session_started_at=runtime.session_started_at.isoformat(),
        tool_metadata=tool_metadata,
    )


def _restore_latest_session(runtime: OhMyRuntime) -> RestoredSessionSummary | None:
    snapshot = load_latest_session_snapshot(runtime.cwd)
    if snapshot is None:
        return None
    messages = snapshot.get("messages", [])
    if not messages:
        return None
    runtime.restore_session_snapshot(snapshot)
    return RestoredSessionSummary(
        session_id=runtime.session_id,
        message_count=len(messages),
        summary=str(snapshot.get("summary") or "").strip(),
        updated_at=float(snapshot["updated_at"]) if isinstance(snapshot.get("updated_at"), (int, float)) else None,
    )


@contextmanager
def _temporary_tool_metadata_flag(runtime: OhMyRuntime, key: str, value: object):
    sentinel = object()
    previous = runtime.engine.tool_metadata.get(key, sentinel)
    runtime.engine.tool_metadata[key] = value
    try:
        yield
    finally:
        if previous is sentinel:
            runtime.engine.tool_metadata.pop(key, None)
        else:
            runtime.engine.tool_metadata[key] = previous


def _handle_restore_command(runtime: OhMyRuntime, transcript: SessionTranscriptWriter, *, session_id: str | None = None) -> None:
    if session_id:
        snapshot = load_session_snapshot_by_id(runtime.cwd, session_id)
        if snapshot is None:
            print_error(f"Session '{session_id}' not found in this workspace.")
            return
        messages = snapshot.get("messages", [])
        if not messages:
            print_error(f"Session '{session_id}' has no messages.")
            return
        runtime.restore_session_snapshot(snapshot)
        _sync_transcript_writer(runtime, transcript)
        print_status(f"Restored session {session_id} with {len(messages)} messages.")
        transcript.record_status("Session", f"Restored {session_id} ({len(messages)} msgs)")
        return

    restored_summary = _restore_latest_session(runtime)
    if restored_summary is None:
        print_status("No saved session is available for this workspace.")
        transcript.record_status("Session", "No saved session available")
        return
    _sync_transcript_writer(runtime, transcript)
    message = f"Restored session {restored_summary.session_id} with {restored_summary.message_count} messages."
    print_status(message)
    transcript.record_status("Session", message)


def _handle_sessions_list(runtime: OhMyRuntime) -> None:
    from datetime import datetime, timezone

    sessions = list_project_sessions(runtime.cwd)
    if not sessions:
        print_status("No saved sessions in this workspace.")
        return
    latest_id = runtime.session_id
    lines: list[str] = [f"# Sessions in {runtime.cwd}"]
    for s in sessions:
        sid = s.get("session_id", "?")
        summary = s.get("summary", "")
        msg_count = s.get("message_count", 0)
        model = s.get("model", "")
        started = s.get("session_started_at", "")
        marker = " <-- current" if sid == latest_id else ""
        line = f"- `{sid}` ({msg_count} msgs, {model})"
        if summary:
            line += f": {summary}"
        if started:
            try:
                dt = datetime.fromisoformat(str(started))
                local_dt = dt.astimezone()
                line += f" — {local_dt.strftime('%m-%d %H:%M')}"
            except ValueError:
                pass
        line += marker
        lines.append(line)
    print_markdown("\n".join(lines))


async def _stream_prompt(runtime: OhMyRuntime, prompt: str, transcript: SessionTranscriptWriter) -> None:
    await _stream_prompt_with_ui(runtime, prompt, transcript, decorated=True)


async def _stream_prompt_with_ui(runtime: OhMyRuntime, prompt: str, transcript: SessionTranscriptWriter, *, decorated: bool) -> None:
    runtime.refresh_system_prompt(prompt)
    _sync_transcript_writer(runtime, transcript)
    transcript.record_user_prompt(prompt)

    output_started = False
    after_tool = False  # blank line needed before next assistant text
    assistant_buffer = ""
    streamed_text = False
    line_start = True
    first_line = True
    use_live_markdown = supports_live_markdown()
    live: object = None  # rich.Live instance while streaming assistant text

    def _stop_live() -> None:
        nonlocal live, assistant_buffer
        if live is not None:
            live.stop()  # type: ignore[union-attr]
            live = None
            assistant_buffer = ""

    try:
        async for event in runtime.engine.submit_message(prompt):
            if isinstance(event, AssistantTextDelta):
                if not output_started and event.text:
                    console().print()
                    output_started = True
                if event.text:
                    if use_live_markdown:
                        assistant_buffer += event.text
                        if live is None:
                            if after_tool:
                                console().print()
                                after_tool = False
                            live = make_live_markdown(assistant_buffer)
                            live.start()  # type: ignore[union-attr]
                        else:
                            update_live_markdown(live, assistant_buffer)  # type: ignore[union-attr]
                    else:
                        if after_tool:
                            console().print()
                            after_tool = False
                        rendered, line_start, first_line = format_assistant_chunk(
                            event.text,
                            line_start=line_start,
                            first_line=first_line,
                        )
                        console().print(rendered, end="")
                        streamed_text = True

            elif isinstance(event, AssistantTurnComplete):
                if not output_started and event.message.text.strip():
                    console().print()
                    output_started = True
                if live is not None:
                    update_live_markdown(live, event.message.text or assistant_buffer)  # type: ignore[union-attr]
                    _stop_live()
                elif event.message.text.strip() and not streamed_text:
                    if after_tool:
                        console().print()
                        after_tool = False
                    print_markdown(event.message.text.strip())
                transcript.record_assistant_message(event.message.text)
                _save_runtime_snapshot(runtime)
                if event.message.text.strip():
                    console().print()
                after_tool = False

            elif isinstance(event, ToolExecutionStarted):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_tool_started(event.tool_name, _tool_preview(event.tool_input))
                transcript.record_tool_started(event.tool_name, event.tool_input)

            elif isinstance(event, ToolExecutionCompleted):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                if event.is_error:
                    first_line = event.output.splitlines()[0] if event.output else "(no output)"
                    print_tool_completed(event.tool_name, is_error=True, detail=f"{event.tool_name}: {first_line}")
                else:
                    print_tool_completed(event.tool_name, is_error=False)
                transcript.record_tool_completed(event.tool_name, event.output, is_error=event.is_error)
                after_tool = True

            elif isinstance(event, SubagentStarted):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_subagent_started(event.role, event.task, read_only=event.read_only)
                transcript.record_status("Subagent", f"started role={event.role} read_only={event.read_only} task={event.task}")

            elif isinstance(event, SubagentCompleted):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_subagent_completed(
                    event.role,
                    event.summary,
                    session_id=event.session_id,
                    is_error=event.is_error,
                    timed_out=event.timed_out,
                )
                transcript.record_status(
                    "Subagent",
                    f"completed role={event.role} session={event.session_id or '(unknown)'} error={event.is_error} timeout={event.timed_out}",
                )

            elif isinstance(event, StatusEvent):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_status(event.message)
                transcript.record_status("Status", event.message)

            elif isinstance(event, ErrorEvent):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_error(event.message)
                transcript.record_status("Error", event.message)

    except MaxTurnsExceeded as exc:
        _stop_live()
        print_status(f"Stopped: reached max_turns={exc.max_turns}")
        transcript.record_status("Stopped", f"reached max_turns={exc.max_turns}")
    finally:
        _stop_live()


def _list_tools_data() -> list[tuple[str, str]]:
    registry = create_tool_registry()
    return [(tool.name, tool.description) for tool in registry.list_tools()]


def _handle_connect_command(runtime: OhMyRuntime, args: str) -> None:
    if not args.strip():
        _connect_interactive(runtime)
        return
    parsed = shlex.split(args)
    opts: dict[str, str] = {}
    i = 0
    while i < len(parsed):
        key = parsed[i]
        if key.startswith("--"):
            key = key[2:]
            i += 1
            if i < len(parsed) and not parsed[i].startswith("--"):
                opts[key] = parsed[i]
                i += 1
            else:
                opts[key] = ""
        else:
            i += 1
    name = opts.get("name", "").strip()
    base_url_v = opts.get("base-url", "").strip()
    api_key_v = opts.get("api-key", "").strip()
    api_format = opts.get("api-format", "").strip()
    model_v = opts.get("model", "").strip()
    effort_v = opts.get("effort", "").strip()
    if not api_format:
        print_error("missing required option: --api-format")
        return
    if not name:
        print_error("missing required option: --name")
        return
    profile = ProviderProfile(
        label=name,
        provider=api_format,
        api_format=api_format,
        default_model=model_v or "gpt-5.4",
        base_url=base_url_v if base_url_v else None,
        last_model=model_v if model_v else None,
    )
    auth_mgr = AuthManager(load_settings())
    auth_mgr.upsert_profile(name, profile)
    if api_key_v:
        auth_mgr.store_profile_credential(name, api_key_v)
    auth_mgr.use_profile(name)
    if effort_v:
        from ohmyself.config.settings import save_settings
        settings_updated = load_settings().model_copy(update={"effort": effort_v})
        save_settings(settings_updated)
    runtime.settings_overrides["active_profile"] = name
    if model_v:
        runtime.settings_overrides["model"] = model_v
    new_settings = runtime.current_settings()
    _, new_profile = new_settings.resolve_profile(name)
    runtime.engine.set_model(new_profile.resolved_model)
    if effort_v:
        runtime.engine.set_effort(effort_v)
    if new_profile.base_url or api_key_v:
        new_client = build_api_client(new_settings, active_profile=name, api_key=(api_key_v if api_key_v else None))
        runtime.engine.set_api_client(new_client)
    runtime.engine.tool_metadata["active_profile"] = name
    print_status(f"已切换到 profile: {name}")


def _connect_interactive(runtime: OhMyRuntime) -> None:
    cons = console()
    settings = runtime.current_settings()
    profile_name, profile = settings.resolve_profile(runtime.settings_overrides.get("active_profile"))
    auth_mgr = AuthManager(settings)
    configured = auth_mgr.get_profile_statuses()[profile_name]["configured"]

    cons.print()
    cons.print("[bold cyan]-- 配置大模型连接（直接回车保留当前值）--[/bold cyan]")

    current_base_url = runtime.current_base_url()
    cons.print(f"  Base URL [当前: {current_base_url or '(未设置)'}]:")
    base_url_input = cons.input("  > ").strip()

    current_key_status = "已配置" if configured else "(未配置)"
    cons.print(f"  API Key [当前: {current_key_status}]:")
    api_key_input = cons.input("  > ").strip()

    current_model = runtime.current_model()
    cons.print(f"  Model [当前: {current_model}]:")
    model_input = cons.input("  > ").strip()

    current_effort = runtime.current_effort()
    cons.print(f"  Effort [当前: {current_effort}]  可选: none / low / medium / high / xhigh:")
    effort_input = cons.input("  > ").strip()

    changed = runtime.reconfigure(
        base_url=base_url_input if base_url_input else None,
        api_key=api_key_input if api_key_input else None,
        model=model_input if model_input else None,
        effort=effort_input if effort_input else None,
    )
    cons.print()
    if changed != "无变更":
        new_settings = runtime.current_settings()
        _, new_profile = new_settings.resolve_profile()
        new_configured = AuthManager(new_settings).get_profile_statuses()[profile_name]["configured"]
        cons.print("[bold green]✓ 配置完成！[/bold green]")
        cons.print(f"  Base URL:  {new_profile.base_url or '(未设置)'}")
        cons.print(f"  Model:     {new_profile.resolved_model}")
        cons.print(f"  Effort:    {new_settings.effort}")
        cons.print(f"  API Key:   {'已配置 ✓' if new_configured else '(未配置)'}")
    else:
        cons.print("[dim]  配置无变更[/dim]")
    cons.print()


def _handle_model_command(runtime: OhMyRuntime, args: str) -> None:
    if not args.strip():
        settings = runtime.current_settings()
        profile_name, profile = settings.resolve_profile(runtime.settings_overrides.get("active_profile"))
        model_history = list(profile.model_history or [])
        other_profiles: list[tuple[str, str, str]] = []
        for pn, p in settings.merged_profiles().items():
            if pn != profile_name:
                other_profiles.append((pn, p.label, p.resolved_model))
        print_model_panel(
            profile_name=profile_name,
            profile_label=profile.label,
            current_model=profile.resolved_model,
            default_model=profile.default_model,
            model_history=model_history,
            other_profiles=other_profiles,
        )
        return
    target = args.strip()
    settings = runtime.current_settings()
    profiles = settings.merged_profiles()
    if target in profiles:
        profile_name, _ = settings.resolve_profile(runtime.settings_overrides.get("active_profile"))
        if target != profile_name:
            auth = AuthManager(settings)
            auth.use_profile(target)
            runtime.settings_overrides["active_profile"] = target
            runtime.settings_overrides.pop("model", None)
            new_settings = runtime.current_settings()
            _, new_profile = new_settings.resolve_profile(target)
            configured = auth.get_profile_statuses()[target]["configured"]
            if new_profile.base_url or not configured:
                new_client = build_api_client(new_settings, active_profile=target)
                runtime.engine.set_api_client(new_client)
            runtime.engine.set_model(new_profile.resolved_model)
            runtime.engine.tool_metadata["active_profile"] = target
            print_status(f"已切换到 Profile: {target}  (模型: {new_profile.resolved_model})")
        else:
            print_status(f"已在当前 Profile: {target}")
        return
    runtime.switch_model(target)
    print_status(f"已切换到模型: {target}")


def _runtime_status_rows(runtime: OhMyRuntime) -> list[tuple[str, str]]:
    settings = runtime.current_settings()
    profile_name, profile = settings.resolve_profile(runtime.settings_overrides.get("active_profile"))
    configured = AuthManager(settings).get_profile_statuses()[profile_name]["configured"]
    goal_topic = ""
    if runtime.goal_context is not None:
        active = runtime.goal_context.active_goal()
        if active:
            goal_topic = active.topic
    return [
        ("config", str(get_settings_path())),
        ("home", str(get_home_dir())),
        ("profile", profile_name),
        ("provider", profile.provider),
        ("model", profile.resolved_model),
        ("effort", settings.effort),
        ("base_url", profile.base_url or "(none)"),
        ("auth", str(configured)),
        ("permission", settings.permission.mode),
        ("tools", str(len(_list_tools_data()))),
        ("workspace", runtime.cwd),
        ("session", runtime.session_id),
        ("goal", goal_topic or "(none)"),
    ]


def _active_goals():
    return [goal for goal in list_goals() if goal.status == "active"]


def _active_goal_topics() -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for goal in _active_goals():
        topic = goal.topic.strip()
        if topic not in seen:
            seen.add(topic)
            topics.append(topic)
    return topics


def _build_plan_prompt() -> str:
    active_goals = _active_goals()
    goal_context = "\n".join(
        f"- {goal.topic}: {goal.description or '(no description)'}"
        for goal in active_goals
    )
    strategy_context = format_strategy_for_prompt()
    status_context = format_recent_status_for_prompt(7)
    coping_context = format_coping_for_prompt()
    return build_plan_organize_prompt(
        goal_context=goal_context,
        active_goal_count=len(active_goals),
        goal_limit=MAX_ACTIVE_GOALS,
        strategy_context=strategy_context,
        status_context=status_context,
        coping_context=coping_context,
    )


async def _continue_pending(runtime: OhMyRuntime, transcript: SessionTranscriptWriter) -> None:
    if not runtime.engine.has_pending_continuation():
        print_status("No paused tool loop is waiting for continuation.")
        return

    output_started = False
    after_tool = False
    assistant_buffer = ""
    streamed_text = False
    line_start = True
    first_line = True
    use_live_markdown = supports_live_markdown()
    live: object = None

    def _stop_live() -> None:
        nonlocal live, assistant_buffer
        if live is not None:
            live.stop()  # type: ignore[union-attr]
            live = None
            assistant_buffer = ""

    try:
        async for event in runtime.engine.continue_pending():
            if isinstance(event, AssistantTextDelta):
                if not output_started and event.text:
                    console().print()
                    output_started = True
                if event.text:
                    if use_live_markdown:
                        assistant_buffer += event.text
                        if live is None:
                            if after_tool:
                                console().print()
                                after_tool = False
                            live = make_live_markdown(assistant_buffer)
                            live.start()  # type: ignore[union-attr]
                        else:
                            update_live_markdown(live, assistant_buffer)  # type: ignore[union-attr]
                    else:
                        if after_tool:
                            console().print()
                            after_tool = False
                        rendered, line_start, first_line = format_assistant_chunk(
                            event.text,
                            line_start=line_start,
                            first_line=first_line,
                        )
                        console().print(rendered, end="")
                        streamed_text = True

            elif isinstance(event, AssistantTurnComplete):
                if not output_started and event.message.text.strip():
                    console().print()
                    output_started = True
                if live is not None:
                    update_live_markdown(live, event.message.text or assistant_buffer)  # type: ignore[union-attr]
                    _stop_live()
                elif event.message.text.strip() and not streamed_text:
                    if after_tool:
                        console().print()
                        after_tool = False
                    print_markdown(event.message.text.strip())
                transcript.record_assistant_message(event.message.text)
                _save_runtime_snapshot(runtime)
                if event.message.text.strip():
                    console().print()
                after_tool = False

            elif isinstance(event, ToolExecutionStarted):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_tool_started(event.tool_name, _tool_preview(event.tool_input))
                transcript.record_tool_started(event.tool_name, event.tool_input)

            elif isinstance(event, ToolExecutionCompleted):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                first_line = event.output.splitlines()[0] if event.output else "(no output)"
                print_tool_completed(
                    event.tool_name,
                    is_error=event.is_error,
                    detail=f"{event.tool_name}: {first_line}" if event.is_error else None,
                )
                transcript.record_tool_completed(event.tool_name, event.output, is_error=event.is_error)
                after_tool = True

            elif isinstance(event, SubagentStarted):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_subagent_started(event.role, event.task, read_only=event.read_only)
                transcript.record_status("Subagent", f"started role={event.role} read_only={event.read_only} task={event.task}")

            elif isinstance(event, SubagentCompleted):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_subagent_completed(
                    event.role,
                    event.summary,
                    session_id=event.session_id,
                    is_error=event.is_error,
                    timed_out=event.timed_out,
                )
                transcript.record_status(
                    "Subagent",
                    f"completed role={event.role} session={event.session_id or '(unknown)'} error={event.is_error} timeout={event.timed_out}",
                )

            elif isinstance(event, StatusEvent):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_status(event.message)
                transcript.record_status("Status", event.message)

            elif isinstance(event, ErrorEvent):
                _stop_live()
                if not output_started:
                    console().print()
                    output_started = True
                print_error(event.message)
                transcript.record_status("Error", event.message)
    finally:
        _stop_live()


async def _handle_user_profile_command(runtime: OhMyRuntime, transcript: SessionTranscriptWriter, instruction: str) -> None:
    if not runtime.engine.messages and not instruction.strip():
        print_error("No conversation history is available for /user_profile.")
        return
    try:
        generated = await generate_user_profile(
            api_client=runtime.engine.api_client,
            model=runtime.engine.model,
            max_tokens=runtime.engine.max_tokens,
            conversation=runtime.engine.messages,
            memory_dir=get_memory_dir(),
            extra_instruction=instruction,
        )
    except Exception as exc:
        print_error(f"Failed to update user profile: {exc}")
        transcript.record_status("User Profile", f"Failed to update user profile: {exc}")
        return
    path = save_user_profile(get_memory_dir(), generated)
    print_status(f"Updated user profile: {path}")
    transcript.record_status("User Profile", f"Updated user profile: {path}")


async def _retrieve_experience_matches(runtime: OhMyRuntime, transcript: SessionTranscriptWriter, question: str, *, goal_id: str | None = None) -> str | None:
    runtime.refresh_system_prompt(question)
    registry = runtime.engine.tool_metadata.get("tool_registry")
    if not isinstance(registry, ToolRegistry):
        print_error("Experience retrieval is unavailable: tool registry is missing.")
        return None
    tool = registry.get("delegate_task")
    if not isinstance(tool, DelegateTaskTool):
        print_error("Experience retrieval is unavailable: delegate_task tool is missing.")
        return None

    task = build_experience_retrieval_task(question, goal_id=goal_id)
    arguments = DelegateTaskToolInput(
        task=task,
        role="experience_retriever",
        allowed_tools=["glob", "read_file", "grep"],
        read_only=True,
        max_turns=6,
        timeout_seconds=90.0,
    )
    print_subagent_started(arguments.role, arguments.task, read_only=arguments.read_only)
    transcript.record_status("Subagent", f"started role={arguments.role} read_only={arguments.read_only} task={arguments.task}")
    result = await tool.execute(
        arguments,
        ToolExecutionContext(
            cwd=Path(runtime.cwd),
            metadata={
                "api_client": runtime.engine.api_client,
                "tool_registry": registry,
                "permission_checker": runtime.engine.permission_checker,
                "model": runtime.engine.model,
                "system_prompt": runtime.engine.system_prompt,
                "max_tokens": runtime.engine.max_tokens,
                "permission_prompt": runtime.engine.permission_prompt,
                **runtime.engine.tool_metadata,
            },
        ),
    )
    subagent = result.metadata.get("subagent")
    if isinstance(subagent, dict):
        session_id = str(subagent.get("session_id") or "")
        summary = str(subagent.get("summary") or result.output).strip()
        timed_out = bool(subagent.get("timed_out", False))
    else:
        session_id = ""
        summary = result.output.strip()
        timed_out = False
    print_subagent_completed(arguments.role, summary, session_id=session_id, is_error=result.is_error, timed_out=timed_out)
    transcript.record_status(
        "Subagent",
        f"completed role={arguments.role} session={session_id or '(unknown)'} error={result.is_error} timeout={timed_out}",
    )
    if result.is_error:
        print_error(summary.splitlines()[0] if summary else "Experience retrieval failed.")
        return None
    return result.output


async def _handle_experience_command(runtime: OhMyRuntime, transcript: SessionTranscriptWriter, instruction: str) -> None:
    cleaned = instruction.strip()
    if not cleaned:
        print_status("Usage: /exper add [content] | /exper [question] | /exper organize")
        return
    if cleaned == "add" or cleaned.startswith("add "):
        content = cleaned[len("add ") :].strip()
        if not content:
            print_error("Usage: /exper add [content]")
            return
        try:
            entry = append_experience(content, goal_id=runtime.active_goal_id)
        except Exception as exc:
            print_error(f"Failed to add experience: {exc}")
            transcript.record_status("Experience", f"Failed to add experience: {exc}")
            return
        print_status(f"Added experience {entry.entry_id}: {entry.path}")
        transcript.record_status("Experience", f"Added {entry.entry_id} to {entry.path}")
        return
    if cleaned == "organize":
        await _stream_prompt(runtime, build_experience_organize_prompt(), transcript)
        return
    if not has_experience_content():
        print_error(f"No experience entries found. Add one with /exper add [content]. Directory: {get_experience_dir()}")
        return
    retrieval_report = await _retrieve_experience_matches(runtime, transcript, cleaned, goal_id=runtime.active_goal_id)
    if retrieval_report is None:
        return
    await _stream_prompt(runtime, build_experience_answer_prompt(cleaned, retrieval_report), transcript)


async def _handle_plan_command(
    runtime: OhMyRuntime,
    transcript: SessionTranscriptWriter,
    instruction: str,
) -> None:
    cleaned = instruction.strip()
    if not cleaned:
        if not has_plan_content() and has_plan_inbox_content():
            await _stream_prompt(runtime, _build_plan_prompt(), transcript)
        content, path = read_today_plan()
        if not content.strip():
            print_status(f"No plan yet. Use /plan [content] to add an item. File: {path}")
            return
        print_markdown(content)
        return
    content = cleaned[len("add ") :].strip() if cleaned.startswith("add ") else cleaned
    if not content:
        print_error("Usage: /plan [content]")
        return
    try:
        entry = append_plan(content)
    except Exception as exc:
        print_error(f"Failed to add plan entry: {exc}")
        transcript.record_status("Plan", f"Failed to add plan entry: {exc}")
        return
    transcript.record_status("Plan", f"Added {entry.entry_id} to {entry.path}")
    with _temporary_tool_metadata_flag(runtime, "auto_allow_write_file_for_plan", True):
        await _stream_prompt(runtime, _build_plan_prompt(), transcript)
    organized, _ = read_today_plan()
    if not organized.strip():
        inbox_content, inbox_path = read_plan_inbox()
        print_error(f"Plan organize did not produce visible content. Inbox preserved at: {inbox_path}")
        transcript.record_status("Plan", f"Organize produced no visible content. Inbox preserved at {inbox_path}")
        if inbox_content.strip():
            print_markdown(inbox_content)
        return
    print_context_snapshot(
        f"Updated plan with: {content}",
        title="Today's Plan",
        markdown=organized,
    )


def _handle_schedule_command(scheduler: SchedulerService, instruction: str) -> None:
    cleaned = instruction.strip()

    if not cleaned or cleaned == "list":
        tasks = scheduler.list_tasks()
        if not tasks:
            print_status("No scheduled tasks.")
            return
        lines = ["# Scheduled Tasks"]
        for t in tasks:
            line = f"- `{t.id}` | {t.type}"
            if t.type == "os_task":
                line += f" | cron={t.cron_expr}"
            elif t.type == "on_shutdown":
                line += " | on OS shutdown detection"
            line += f" | {t.status} | {t.prompt[:60]}"
            lines.append(line)
        print_markdown("\n".join(lines))
        return

    if cleaned.startswith("cancel "):
        task_id = cleaned[len("cancel "):].strip()
        if scheduler.cancel_task(task_id):
            print_success(f"Task {task_id} cancelled.")
        else:
            print_error(f"Task {task_id} not found or already completed.")
        return

    if cleaned.startswith("shutdown "):
        prompt = cleaned[len("shutdown "):].strip()
        if not prompt:
            print_error("Usage: /schedule shutdown <prompt>")
            return
        task = scheduler.schedule_on_shutdown(prompt)
        print_success(f"Shutdown task scheduled. ID: {task.id}")
        return

    if cleaned.startswith("os "):
        _handle_schedule_os_command(scheduler, cleaned[len("os "):].strip())
        return

    if cleaned == "help":
        help_text = """# /schedule Commands
- `/schedule` or `/schedule list` — list all tasks
- `/schedule os add <cron> <prompt>` — OS-level reminder (Windows Task Scheduler, fires even when ohmy is closed)
- `/schedule os list` — list OS tasks
- `/schedule os remove <id>` — remove OS task
- `/schedule shutdown <prompt>` — run on OS shutdown detection
- `/schedule cancel <id>` — cancel any task"""
        print_markdown(help_text)
        return

    print_error(f"Unknown schedule command: {instruction}. Use /schedule help for usage.")


def _handle_schedule_os_command(scheduler: SchedulerService, instruction: str) -> None:
    cleaned = instruction.strip()

    if not cleaned or cleaned == "list":
        tasks = scheduler.list_os_tasks()
        if not tasks:
            print_status("No OS-level scheduled tasks.")
            return
        lines = ["# OS Scheduled Tasks (fire even when ohmy is closed)"]
        for t in tasks:
            line = f"- `{t.id}` | cron={t.cron_expr} | {t.status} | {t.prompt[:60]}"
            lines.append(line)
        print_markdown("\n".join(lines))
        return

    if cleaned.startswith("remove "):
        task_id = cleaned[len("remove "):].strip()
        if scheduler.remove_os_task(task_id):
            print_success(f"OS task {task_id} removed from Windows Task Scheduler.")
        else:
            print_error(f"OS task {task_id} not found.")
        return

    if cleaned.startswith("add "):
        rest = cleaned[len("add "):].strip()
        i = 0
        while i < len(rest) and rest[i] != " ":
            i += 1
        cron_expr = rest[:i].strip()
        prompt = rest[i:].strip()
        if not cron_expr or not prompt:
            print_error("Usage: /schedule os add <cron> <reminder>")
            return
        try:
            task = scheduler.schedule_on_os(cron_expr, prompt)
            print_success(f"OS reminder registered.\nID: {task.id}\nCron: {task.cron_expr}\nFires even when ohmy is closed.")
        except (ValueError, RuntimeError) as exc:
            print_error(f"Failed to register OS task: {exc}")
        return

    print_error(f"Unknown os subcommand: {instruction}. Use: add, list, remove")


async def _handle_goal_command(
    runtime: OhMyRuntime,
    transcript: SessionTranscriptWriter,
    instruction: str,
) -> None:
    cleaned = instruction.strip()

    if not cleaned:
        print_markdown(format_goals_markdown())
        return

    if cleaned.startswith("switch ") or cleaned == "switch":
        goal_id = cleaned[len("switch "):].strip() if cleaned.startswith("switch ") else ""
        if not goal_id:
            print_error("Usage: /goal switch [id]")
            return
        _perform_goal_switch(runtime, transcript, goal_id)
        return

    if cleaned == "exit":
        await _perform_goal_exit(runtime, transcript)
        return

    if cleaned == "memory":
        _handle_goal_memory_show(runtime)
        return

    if cleaned == "memory update":
        await _handle_goal_memory_update(runtime, transcript)
        return

    if cleaned.startswith("memory search "):
        query = cleaned[len("memory search "):].strip()
        if not query:
            print_error("Usage: /goal memory search [query] [--deep]")
            return
        deep = " --deep" in query
        if deep:
            query = query.replace(" --deep", "").strip()
        await _handle_goal_memory_search(runtime, transcript, query, deep=deep)
        return

    if cleaned.startswith("memory search"):
        print_error("Usage: /goal memory search [query] [--deep]")
        return

    if cleaned == "sessions":
        _handle_goal_sessions_show(runtime)
        return

    if cleaned == "progress":
        if not runtime.goal_context or not runtime.goal_context.active_goal_id:
            print_error("Not in goal mode. Use /goal progress [id] or enter a goal first.")
            return
        _show_goal_progress_history(runtime.goal_context.active_goal_id)
        return

    if cleaned.startswith("progress "):
        parts = cleaned.split()
        if len(parts) == 2:
            _show_goal_progress_history(parts[1])
            return
        if len(parts) != 3:
            print_error("Usage: /goal progress | /goal progress [id] | /goal progress [id] [0-100]")
            return
        try:
            entry = update_goal_progress(parts[1], int(parts[2]))
        except Exception as exc:
            print_error(f"Failed to update goal progress: {exc}")
            transcript.record_status("Goal", f"Failed to update goal progress: {exc}")
            return
        print_context_snapshot(
            f"Updated goal {entry.entry_id}: {entry.progress_percent}%",
            title="Goals",
            markdown=format_goals_markdown(),
        )
        transcript.record_status("Goal", f"Updated {entry.entry_id} to {entry.progress_percent}%")
        return

    if cleaned.startswith("done "):
        parts = cleaned.split()
        if len(parts) != 2:
            print_error("Usage: /goal done [id]")
            return
        try:
            entry = complete_goal(parts[1])
        except Exception as exc:
            print_error(f"Failed to complete goal: {exc}")
            transcript.record_status("Goal", f"Failed to complete goal: {exc}")
            return
        print_context_snapshot(
            f"Completed goal {entry.entry_id}.",
            title="Goals",
            markdown=format_goals_markdown(),
        )
        transcript.record_status("Goal", f"Completed {entry.entry_id}")
        return

    if cleaned.startswith("stop "):
        parts = cleaned.split()
        if len(parts) != 2:
            print_error("Usage: /goal stop [id]")
            return
        try:
            entry = stop_goal(parts[1])
        except Exception as exc:
            print_error(f"Failed to stop goal: {exc}")
            transcript.record_status("Goal", f"Failed to stop goal: {exc}")
            return
        print_context_snapshot(
            f"Stopped goal {entry.entry_id}.",
            title="Goals",
            markdown=format_goals_markdown(),
        )
        transcript.record_status("Goal", f"Stopped {entry.entry_id}")
        return

    try:
        topic, description, ends_at, progress_percent, linked_dir = _parse_goal_add_arguments(cleaned)
        normalized_linked_dir = _normalize_goal_linked_dir(linked_dir, base_cwd=_runtime_base_cwd(runtime))
        entry = append_goal(
            topic,
            description=description,
            ends_at=ends_at,
            progress_percent=progress_percent,
            linked_dir=normalized_linked_dir,
        )
    except Exception as exc:
        print_error(f"Failed to add goal: {exc}")
        transcript.record_status("Goal", f"Failed to add goal: {exc}")
        return
    print_context_snapshot(
        f"Added goal {entry.entry_id}: {entry.path}",
        title="Goals",
        markdown=format_goals_markdown(),
    )
    transcript.record_status("Goal", f"Added {entry.entry_id} to {entry.path}")


def _perform_goal_switch(runtime: OhMyRuntime, transcript: SessionTranscriptWriter, goal_id: str) -> None:
    if runtime.goal_context is None:
        print_error("Goal context is not available.")
        return
    if runtime.goal_context.active_goal_id is not None:
        _save_runtime_snapshot(runtime)
        runtime.goal_context.record_session_link(
            runtime.session_id,
            cwd=runtime.cwd,
            model=runtime.current_model(),
            message_count=len(runtime.engine.messages),
        )
    goal = runtime.goal_context.switch_to(goal_id)
    if goal is None:
        print_error(f"Goal not found: {goal_id}. Use /goal to list active goals.")
        return
    target_cwd, active_linked_dir, cwd_warning = _resolve_goal_runtime_cwd(runtime, goal.linked_dir)
    runtime.set_cwd(target_cwd, linked_dir=active_linked_dir)
    _save_runtime_snapshot(runtime)
    runtime.active_goal_id = goal_id
    runtime.engine.tool_metadata["active_goal_id"] = goal_id
    runtime.engine.clear()
    runtime.start_new_session()
    runtime.engine.tool_metadata["active_goal_id"] = goal_id
    _sync_transcript_writer(runtime, transcript)
    runtime.refresh_system_prompt()
    _save_runtime_snapshot(runtime)
    print_goal_switch_feedback(goal.topic)
    if cwd_warning:
        print_status(cwd_warning)
    transcript.record_status("Goal", f"Switched to {goal.entry_id} ({goal.topic}) cwd={runtime.cwd}")


async def _perform_goal_exit(runtime: OhMyRuntime, transcript: SessionTranscriptWriter) -> None:
    if runtime.goal_context is None:
        print_error("Goal context is not available.")
        return
    if runtime.goal_context.active_goal_id is None:
        print_status("Not currently in goal mode.")
        return
    await _maybe_prompt_memory_update_on_leave(runtime, transcript)
    _save_runtime_snapshot(runtime)
    runtime.goal_context.exit_goal()
    runtime.active_goal_id = None
    runtime.set_cwd(_runtime_base_cwd(runtime), linked_dir=None)
    runtime.engine.clear()
    runtime.start_new_session()
    runtime.engine.tool_metadata["active_goal_id"] = None
    _sync_transcript_writer(runtime, transcript)
    runtime.refresh_system_prompt()
    _save_runtime_snapshot(runtime)
    print_goal_switch_feedback(None)
    transcript.record_status("Goal", "Exited goal mode")


async def _maybe_prompt_memory_update_on_leave(runtime: OhMyRuntime, transcript: SessionTranscriptWriter) -> None:
    if not runtime.active_goal_id:
        return
    if not _has_meaningful_conversation(runtime):
        return
    if not prompt_goal_memory_update():
        return
    await _handle_goal_memory_update(runtime, transcript)


async def _maybe_prompt_normal_memory_update(runtime: OhMyRuntime, transcript: SessionTranscriptWriter) -> None:
    if not _has_meaningful_conversation(runtime):
        return
    from rich.prompt import Confirm

    console().print()
    if not Confirm.ask(
        "当前对话有新的内容，是否更新个人记忆？",
        console=console(),
        default=True,
    ):
        return
    from ohmyself.config.paths import get_memory_dir
    from ohmyself.services.user_profile import generate_user_profile, save_user_profile

    print_status("正在分析对话更新个人记忆...")
    try:
        memory_dir = get_memory_dir()
        profile = await generate_user_profile(
            api_client=runtime.engine.api_client,
            model=runtime.engine.model,
            max_tokens=runtime.engine.max_tokens,
            conversation=runtime.engine.messages,
            memory_dir=memory_dir,
        )
        save_user_profile(memory_dir, profile)
        print_success("个人记忆已更新。")
        transcript.record_status("Memory", "Updated user_profile.md")
    except Exception as exc:
        print_error(f"Failed to update user memory: {exc}")
        transcript.record_status("Memory", f"Update failed: {exc}")


def _handle_goal_memory_show(runtime: OhMyRuntime) -> None:
    goal_id = runtime.active_goal_id
    if not goal_id:
        print_error("Not in goal mode. Use /goal switch [id] to enter a goal first.")
        return
    lines: list[str] = [f"# Goal Memory for {goal_id}"]
    for filename, label in [
        (AI_NOTES_FILENAME, "AI Notes"),
        (USER_PREFS_FILENAME, "User Preferences"),
        (CONTEXT_FILENAME, "Context"),
    ]:
        content = read_goal_memory(goal_id, filename)
        if content.strip():
            lines.append(f"\n## {label}\n\n{content}")
        else:
            lines.append(f"\n## {label}\n\n(empty)")
    print_markdown("\n".join(lines))


def _show_goal_progress_history(goal_id: str) -> None:
    goals = list_goals()
    goal = next((g for g in goals if g.entry_id == goal_id), None)
    if goal is None:
        print_error(f"Goal not found: {goal_id}")
        return
    lines: list[str] = [
        f"# Progress History for {goal.topic}",
        f"`{goal.entry_id}`  status={goal.status}  progress={goal.progress_percent}%",
        "",
        "| Date | Progress | Event | Note |",
        "|------|----------|-------|------|",
    ]
    for record in sorted(goal.progress_history, key=lambda r: r.recorded_at):
        local = record.recorded_at.astimezone()
        date_str = local.strftime("%m-%d %H:%M")
        lines.append(f"| {date_str} | {record.progress_percent}% | {record.event} | {record.note or '-'} |")
    print_markdown("\n".join(lines))


def _handle_goal_sessions_show(runtime: OhMyRuntime) -> None:
    goal_id = runtime.active_goal_id
    if not goal_id:
        print_error("Not in goal mode. Use /goal switch [id] to enter a goal first.")
        return
    sessions = list_goal_sessions(goal_id)
    if not sessions:
        print_status(f"No sessions linked to goal {goal_id} yet.")
        return
    lines: list[str] = [f"# Sessions linked to {goal_id}"]
    for s in sessions:
        sid = s.get("session_id", "unknown")
        linked = s.get("linked_at", "")
        summary = s.get("summary", "").strip()
        msg_count = s.get("message_count", 0)
        line = f"- `{sid}` ({linked}) - {msg_count} msgs"
        if summary:
            line += f": {summary[:80]}"
        lines.append(line)
    print_markdown("\n".join(lines))


def _has_meaningful_conversation(runtime: OhMyRuntime) -> bool:
    for msg in runtime.engine.messages:
        if msg.role == "user" and msg.text.strip():
            return True
    return False


async def _handle_goal_memory_update(runtime: OhMyRuntime, transcript: SessionTranscriptWriter) -> None:
    goal_id = runtime.active_goal_id
    if not goal_id:
        print_error("Not in goal mode. Use /goal switch [id] to enter a goal first.")
        return
    goal_context = runtime.goal_context
    if goal_context is None:
        print_error("Goal context is not available.")
        return
    goal = goal_context.active_goal()
    if goal is None:
        print_error("Active goal not found.")
        return
    if not runtime.engine.messages:
        print_status("No conversation to analyze.")
        return
    print_status("Updating goal memory from conversation...")
    try:
        updates = await update_goal_memory_via_ai(
            goal_id=goal_id,
            api_client=runtime.engine.api_client,
            model=runtime.engine.model,
            max_tokens=runtime.engine.max_tokens,
            conversation=runtime.engine.messages,
            goal_entry=goal,
        )
    except Exception as exc:
        print_error(f"Failed to update goal memory: {exc}")
        transcript.record_status("Goal Memory", f"Update failed: {exc}")
        return
    if updates:
        updated_files = ", ".join(updates.keys())
        print_status(f"Goal memory updated: {updated_files}")
        transcript.record_status("Goal Memory", f"Updated: {updated_files}")
    else:
        print_status("No new memory to add.")
        transcript.record_status("Goal Memory", "No updates needed")


async def _handle_goal_memory_search(
    runtime: OhMyRuntime,
    transcript: SessionTranscriptWriter,
    query: str,
    *,
    deep: bool = False,
) -> None:
    goal_id = runtime.active_goal_id
    if not goal_id:
        print_error("Not in goal mode. Use /goal switch [id] to enter a goal first.")
        return
    registry = runtime.engine.tool_metadata.get("tool_registry")
    if not isinstance(registry, ToolRegistry):
        print_error("Memory search is unavailable: tool registry is missing.")
        return
    tool = registry.get("delegate_task")
    if not isinstance(tool, DelegateTaskTool):
        print_error("Memory search is unavailable: delegate_task tool is missing.")
        return

    task = build_goal_memory_retrieval_task(goal_id, query, deep=deep)
    arguments = DelegateTaskToolInput(
        task=task,
        role="goal_memory_retriever",
        allowed_tools=["glob", "read_file", "grep"],
        read_only=True,
        max_turns=6,
        timeout_seconds=90.0,
    )
    print_subagent_started(arguments.role, arguments.task, read_only=arguments.read_only)
    transcript.record_status("Subagent", f"started role={arguments.role}")
    result = await tool.execute(
        arguments,
        ToolExecutionContext(
            cwd=Path(runtime.cwd),
            metadata={
                "api_client": runtime.engine.api_client,
                "tool_registry": registry,
                "permission_checker": runtime.engine.permission_checker,
                "model": runtime.engine.model,
                "system_prompt": runtime.engine.system_prompt,
                "max_tokens": runtime.engine.max_tokens,
                "permission_prompt": runtime.engine.permission_prompt,
                **runtime.engine.tool_metadata,
            },
        ),
    )
    subagent = result.metadata.get("subagent")
    if isinstance(subagent, dict):
        session_id = str(subagent.get("session_id") or "")
        summary = str(subagent.get("summary") or result.output).strip()
        timed_out = bool(subagent.get("timed_out", False))
    else:
        session_id = ""
        summary = result.output.strip()
        timed_out = False
    print_subagent_completed(arguments.role, summary, session_id=session_id, is_error=result.is_error, timed_out=timed_out)
    transcript.record_status("Subagent", f"completed role={arguments.role}")
    if result.is_error:
        print_error(summary.splitlines()[0] if summary else "Memory search failed.")
        return
    print_markdown(result.output)


def _parse_goal_add_arguments(raw: str) -> tuple[str, str, date | None, int, str | None]:
    tokens = [_strip_matching_quotes(token) for token in shlex.split(raw, posix=False)]
    args = tokens[1:] if tokens and tokens[0] == "add" else tokens
    topic_parts: list[str] = []
    description = ""
    ends_at: date | None = None
    progress_percent = 0
    linked_dir: str | None = None
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--ends":
            index += 1
            if index >= len(args):
                raise ValueError("missing value for --ends")
            ends_at = date.fromisoformat(args[index])
        elif item == "--progress":
            index += 1
            if index >= len(args):
                raise ValueError("missing value for --progress")
            progress_percent = int(args[index])
        elif item in {"--desc", "--description"}:
            index += 1
            if index >= len(args):
                raise ValueError("missing value for --desc")
            description = args[index].strip()
        elif item == "--dir":
            index += 1
            if index >= len(args):
                raise ValueError("missing value for --dir")
            linked_dir = args[index].strip() or None
        else:
            topic_parts.append(item)
        index += 1
    topic = " ".join(topic_parts).strip()
    if not topic:
        raise ValueError("goal topic cannot be empty")
    if not description:
        for separator in ("：", ":"):
            if separator in topic:
                left, right = topic.split(separator, 1)
                left = left.strip()
                right = right.strip()
                if left and right:
                    topic = left
                    description = right
                    break
    return topic, description, ends_at, progress_percent, linked_dir


def _runtime_base_cwd(runtime: OhMyRuntime) -> str:
    return str(Path(getattr(runtime, "base_cwd", runtime.cwd)).expanduser().resolve())


def _normalize_goal_linked_dir(linked_dir: str | None, *, base_cwd: str) -> str | None:
    if not linked_dir:
        return None
    path = Path(linked_dir).expanduser()
    if not path.is_absolute():
        path = Path(base_cwd) / path
    return str(path.resolve())


def _resolve_goal_runtime_cwd(runtime: OhMyRuntime, linked_dir: str | None) -> tuple[str, str | None, str | None]:
    base_cwd = _runtime_base_cwd(runtime)
    normalized_linked_dir = _normalize_goal_linked_dir(linked_dir, base_cwd=base_cwd)
    if not normalized_linked_dir:
        return base_cwd, None, None
    path = Path(normalized_linked_dir)
    if path.exists() and path.is_dir():
        return str(path), str(path), None
    return base_cwd, None, f"Goal linked_dir is unavailable, staying in workspace: {normalized_linked_dir}"


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


@auth_app.command("login")
def auth_login(target: str | None = typer.Argument(None, help="Profile name, or omit to use the active profile.")) -> None:
    manager = AuthManager()
    profile_name = target or manager.get_active_profile()
    profiles = manager.list_profiles()
    if profile_name not in profiles:
        raise typer.BadParameter(f"Unknown provider profile: {profile_name}")
    key = typer.prompt(f"Enter API key for {profile_name}", hide_input=True)
    manager.store_profile_credential(profile_name, key)
    print(f"Saved API key for {profile_name}.", flush=True)


@auth_app.command("status")
def auth_status() -> None:
    statuses = AuthManager().get_profile_statuses()
    for name, info in statuses.items():
        marker = "*" if info["active"] else " "
        configured = "ready" if info["configured"] else "missing auth"
        print(f"{marker} {name}: {info['label']} [{configured}]")
        print(f"    provider={info['provider']} auth={info['auth_source']} model={info['model']}")


@auth_app.command("logout")
def auth_logout(target: str | None = typer.Argument(None, help="Profile name, or omit to use the active profile.")) -> None:
    manager = AuthManager()
    profile_name = target or manager.get_active_profile()
    manager.clear_profile_credential(profile_name)
    print(f"Cleared API key for {profile_name}.", flush=True)


@provider_app.command("list")
def provider_list() -> None:
    statuses = AuthManager().get_profile_statuses()
    for name, info in statuses.items():
        marker = "*" if info["active"] else " "
        print(f"{marker} {name}: {info['label']}")
        print(f"    provider={info['provider']} model={info['model']} base_url={info['base_url'] or '(default)'}")


@provider_app.command("use")
def provider_use(name: str = typer.Argument(..., help="Profile name")) -> None:
    manager = AuthManager()
    manager.use_profile(name)
    print(f"Activated provider profile: {name}", flush=True)


@provider_app.command("add")
def provider_add(
    name: str = typer.Argument(..., help="Provider profile name"),
    label: str = typer.Option(..., "--label"),
    provider: str = typer.Option(..., "--provider"),
    api_format: str = typer.Option(..., "--api-format"),
    model: str = typer.Option(..., "--model"),
    base_url: str | None = typer.Option(None, "--base-url"),
) -> None:
    manager = AuthManager()
    manager.upsert_profile(
        name,
        ProviderProfile(
            label=label,
            provider=provider,
            api_format=api_format,
            default_model=model,
            last_model=model,
            base_url=base_url,
        ),
    )
    print(f"Saved provider profile: {name}", flush=True)


@provider_app.command("edit")
def provider_edit(
    name: str = typer.Argument(..., help="Provider profile name"),
    label: str | None = typer.Option(None, "--label"),
    provider: str | None = typer.Option(None, "--provider"),
    api_format: str | None = typer.Option(None, "--api-format"),
    model: str | None = typer.Option(None, "--model"),
    base_url: str | None = typer.Option(None, "--base-url"),
) -> None:
    AuthManager().update_profile(name, label=label, provider=provider, api_format=api_format, model=model, base_url=base_url)
    print(f"Updated provider profile: {name}", flush=True)


@provider_app.command("remove")
def provider_remove(name: str = typer.Argument(..., help="Provider profile name")) -> None:
    AuthManager().remove_profile(name)
    print(f"Removed provider profile: {name}", flush=True)


@app.command("tools")
def tools_command() -> None:
    print_tools_panel(_list_tools_data())


@app.command("status")
def status_command() -> None:
    settings = load_settings()
    manager = AuthManager(settings)
    profile_name, profile = settings.resolve_profile()
    print_status_panel(
        [
            ("config", str(get_settings_path())),
            ("home", str(get_home_dir())),
            ("profile", profile_name),
            ("provider", profile.provider),
            ("model", profile.resolved_model),
            ("auth", str(manager.get_profile_statuses()[profile_name]["configured"])),
            ("permission", settings.permission.mode),
            ("tools", str(len(_list_tools_data()))),
        ]
    )


async def _maybe_daily_progress_update(runtime: OhMyRuntime) -> None:
    from datetime import date, timedelta

    from ohmyself.services.plan import get_plan_path, read_text_file_robust

    today = date.today()
    last_check = get_last_progress_check_date()
    if last_check is not None and last_check >= today:
        return

    yesterday = today - timedelta(days=1)
    plan_path = get_plan_path(yesterday)
    if not plan_path.exists():
        set_last_progress_check_date(today)
        return
    plan_content = read_text_file_robust(plan_path)
    if not plan_content.strip():
        set_last_progress_check_date(today)
        return

    active_goals = [g for g in list_goals() if g.status == "active"]
    if not active_goals:
        set_last_progress_check_date(today)
        return

    print_status("Assessing goal progress from yesterday's plan...")
    try:
        updates = await assess_daily_goal_progress(
            api_client=runtime.engine.api_client,
            model=runtime.engine.model,
            max_tokens=runtime.engine.max_tokens,
            yesterday=yesterday,
            plan_content=plan_content,
            active_goals=active_goals,
        )
    except Exception:
        set_last_progress_check_date(today)
        return

    if not updates:
        set_last_progress_check_date(today)
        return

    for item in updates:
        goal_id = str(item.get("goal_id", ""))
        new_progress = int(item.get("new_progress", -1))
        note = str(item.get("note", ""))
        if not goal_id or new_progress < 0 or new_progress > 100:
            continue
        try:
            update_goal_progress(goal_id, new_progress, note=note)
        except Exception:
            continue

    goal_names = []
    for item in updates:
        gid = str(item.get("goal_id", ""))
        for g in active_goals:
            if g.entry_id == gid:
                goal_names.append(f"{g.topic} → {item.get('new_progress')}%")
                break
    if goal_names:
        print_status(f"Daily progress updated: {', '.join(goal_names)}")
    set_last_progress_check_date(today)


_STATUS_PARSE_SYSTEM_PROMPT = """\
You are a personal status analyst. Parse the user's natural language status update into structured data.

The user describes their current personal state across several dimensions, plus their future expectations.
Available status fields: {fields}

Return ONLY a JSON object (no markdown wrapping, no extra text):
{{
  "fields": {{"睡眠情况": "...", "身体情况": "...", ...}},
  "future_expectation": "user's expectation for the near future",
  "risks": ["identified risk 1", "identified risk 2"],
  "preparations": ["suggested preparation 1", "suggested preparation 2"],
  "notes": "any additional notes or concerns"
}}

Guidelines:
- For each available field, extract the value from the user's message. If not mentioned, use "未提及".
- future_expectation: what the user expects in the coming days/weeks.
- risks: based on the user's current status and expectations, identify 1-3 potential risks (fatigue, emotional burnout, health issues, etc.).
- preparations: for each risk, suggest a concrete preparation or mitigation.
- notes: capture any other relevant context the user shared.
- Be concise but thorough. Risks and preparations should be actionable."""


def _build_status_parse_prompt(user_message: str) -> str:
    fields = get_status_fields()
    fields_text = ", ".join(fields)
    prompt = _STATUS_PARSE_SYSTEM_PROMPT.format(fields=fields_text)
    return prompt + f"\n\nUser message:\n{user_message}"


async def _parse_status_from_message(
    runtime: OhMyRuntime,
    user_message: str,
    existing_entry: StatusEntry | None,
) -> StatusEntry | None:
    from datetime import datetime

    from ohmyself.api.client import ApiMessageCompleteEvent, ApiMessageRequest
    from ohmyself.engine.messages import ConversationMessage

    today = date.today()
    system_prompt = _STATUS_PARSE_SYSTEM_PROMPT.format(fields=", ".join(get_status_fields()))

    user_text = user_message.strip()
    if existing_entry:
        user_text = f"Previous status: {json.dumps(existing_entry.to_payload(), ensure_ascii=False)}\n\nUpdate with: {user_text}"

    request = ApiMessageRequest(
        model=runtime.engine.model,
        messages=[ConversationMessage.from_user_text(user_text)],
        system_prompt=system_prompt,
        max_tokens=runtime.engine.max_tokens,
    )
    result_text = ""
    async for event in runtime.engine.api_client.stream_message(request):
        if isinstance(event, ApiMessageCompleteEvent):
            result_text = event.message.text.strip()

    if not result_text:
        return None

    try:
        import re
        json_match = re.search(r"\{.*\}", result_text, re.DOTALL)
        if json_match:
            result_text = json_match.group(0)
        payload = json.loads(result_text)
        return StatusEntry(
            date=today.isoformat(),
            fields=payload.get("fields") or {},
            future_expectation=str(payload.get("future_expectation", "")),
            risks=payload.get("risks") or [],
            preparations=payload.get("preparations") or [],
            notes=str(payload.get("notes", "")),
            updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


_COPING_MATCH_SYSTEM_PROMPT = """\
You are a coping strategy advisor. Given the user's current personal status and their coping rulebook, identify which rules apply.

Return ONLY a JSON array of applicable rule descriptions (no markdown wrapping, no extra text):
["rule description 1", "rule description 2"]

If no rules match, return an empty array: []
Only match rules that are clearly relevant to the current status."""


async def _match_coping_rules(
    runtime: OhMyRuntime,
    status_entry: StatusEntry,
) -> str:
    from ohmyself.api.client import ApiMessageCompleteEvent, ApiMessageRequest
    from ohmyself.engine.messages import ConversationMessage

    coping_content = read_coping()
    if not coping_content.strip():
        return ""

    status_text = json.dumps(status_entry.to_payload(), ensure_ascii=False)
    user_text = f"Coping rules:\n{coping_content}\n\nCurrent status:\n{status_text}"

    request = ApiMessageRequest(
        model=runtime.engine.model,
        messages=[ConversationMessage.from_user_text(user_text)],
        system_prompt=_COPING_MATCH_SYSTEM_PROMPT,
        max_tokens=runtime.engine.max_tokens,
    )
    result_text = ""
    async for event in runtime.engine.api_client.stream_message(request):
        if isinstance(event, ApiMessageCompleteEvent):
            result_text = event.message.text.strip()

    if not result_text:
        return ""

    try:
        import re
        json_match = re.search(r"\[.*\]", result_text, re.DOTALL)
        if json_match:
            result_text = json_match.group(0)
        rules = json.loads(result_text)
        if isinstance(rules, list) and rules:
            lines = ["\n## 相关应对策略"]
            for r in rules:
                lines.append(f"- {r}")
            return "\n".join(lines)
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


_DAILY_PLAN_GENERATE_PROMPT = """\
You are a personal daily planner. Generate today's plan based on the user's context.

Guidelines:
- Consider the user's long-term strategy and ensure today's actions align with it.
- Consider the user's current personal status (energy, emotions, health) and adjust plan difficulty accordingly.
- Consider active goals and their progress — push forward where momentum exists.
- Consider risks identified in the status and include mitigation actions.
- Consider relevant coping strategies.
- Look at yesterday's plan for continuity.
- The plan should be realistic for one day. Prioritize the most important 3-5 items.
- If the user is low energy or not well, suggest rest and recovery items.
- Structure: ## Focus (1 top priority), ## Tasks (3-5 items), ## Notes.

User context:
{context}

Write the plan to: {plan_path}
Use the write_file tool (not shell commands). Always write with UTF-8 encoding.
Reply with a brief summary of the plan (1-2 sentences)."""


async def _generate_daily_plan(
    runtime: OhMyRuntime,
    transcript: SessionTranscriptWriter,
    status_entry: StatusEntry,
) -> None:
    from datetime import timedelta

    today = date.today()
    yesterday = today - timedelta(days=1)

    strategy_text = format_strategy_for_prompt()
    status_text = json.dumps(status_entry.to_payload(), ensure_ascii=False)
    goals_text = format_goals_markdown(_active_goals())
    coping_text = format_coping_for_prompt()

    from ohmyself.services.plan import get_plan_path, read_text_file_robust
    yesterday_plan = ""
    yesterday_path = get_plan_path(yesterday)
    if yesterday_path.exists():
        yesterday_plan = read_text_file_robust(yesterday_path)

    today_plan_content, _ = read_today_plan()

    context = f"""\
## 长期战略
{strategy_text}

## 当前个人状态
{status_text}

## 活跃目标
{goals_text}

## 应对策略
{coping_text}

## 昨日计划
{yesterday_plan if yesterday_plan.strip() else "(无)"}

## 今日已有计划
{today_plan_content if today_plan_content.strip() else "(尚未设定)"}"""

    plan_path = get_plan_path(today)
    prompt = _DAILY_PLAN_GENERATE_PROMPT.format(context=context, plan_path=plan_path)

    print_status("基于当前状态生成今日计划...")
    await _stream_prompt(runtime, prompt, transcript)


async def _daily_status_review(
    runtime: OhMyRuntime,
    transcript: SessionTranscriptWriter,
) -> None:
    fields = get_status_fields()
    recent = get_recent_status(7)

    if recent:
        console().print()
        print_markdown(format_recent_status_table(7))

    today_entry = get_today_status()
    if today_entry:
        console().print()
        print_markdown(format_today_status_markdown())
        print_status("今日状态已记录。如需调整请输入内容，或输入 skip 跳过。")
    else:
        fields_display = "、".join(fields)
        print_status(f"请描述今日状态（{fields_display}、未来预期等），或输入 skip 跳过。")

    line = await asyncio.to_thread(input, "> ")
    stripped = line.strip()
    if not stripped or stripped.lower() == "skip":
        console().print()
        return

    print_status("解析状态中...")
    status_entry = await _parse_status_from_message(runtime, stripped, today_entry)
    if status_entry is None:
        print_error("无法解析状态信息，请稍后使用 /status 手动更新。")
        return

    save_status(status_entry)
    console().print()
    print_markdown(format_today_status_markdown())
    print_success("状态已保存。")

    if has_coping_content():
        matches = await _match_coping_rules(runtime, status_entry)
        if matches:
            print_markdown(matches)

    await _generate_daily_plan(runtime, transcript, status_entry)


async def _handle_status_command(
    runtime: OhMyRuntime,
    transcript: SessionTranscriptWriter,
    instruction: str,
) -> None:
    cleaned = instruction.strip()

    if not cleaned:
        print_markdown(format_today_status_markdown())
        recent = get_recent_status(7)
        if len(recent) > 1:
            console().print()
            print_markdown("## 近期趋势\n\n" + format_recent_status_table(7))
        return

    if cleaned == "system":
        print_status_panel(_runtime_status_rows(runtime))
        return

    if cleaned.startswith("fields "):
        sub = cleaned[len("fields "):].strip()
        fields = get_status_fields()
        if sub.startswith("add "):
            name = sub[len("add "):].strip()
            if not name:
                print_error("Usage: /status fields add <name>")
                return
            if name in fields:
                print_error(f"Field '{name}' already exists.")
                return
            fields.append(name)
            save_status_fields(fields)
            print_success(f"Field '{name}' added. Current fields: {', '.join(fields)}")
            return
        if sub.startswith("remove "):
            name = sub[len("remove "):].strip()
            if not name:
                print_error("Usage: /status fields remove <name>")
                return
            if name not in fields:
                print_error(f"Field '{name}' not found.")
                return
            if len(fields) <= 1:
                print_error("Cannot remove the last field.")
                return
            fields.remove(name)
            save_status_fields(fields)
            print_success(f"Field '{name}' removed. Current fields: {', '.join(fields)}")
            return
        if sub == "list":
            print_status(f"Current fields: {', '.join(fields)}")
            return
        print_error("Usage: /status fields [add|remove|list]")
        return

    if cleaned == "update":
        await _daily_status_review(runtime, transcript)
        return

    print_error("Usage: /status | /status system | /status update | /status fields [add|remove|list]")


async def _handle_strategy_command(runtime: OhMyRuntime, instruction: str) -> None:
    cleaned = instruction.strip()
    if cleaned:
        print_error("Usage: /strategy — 查看当前长期战略。战略应通过日常对话中与AI讨论后自然调整。")
        return
    content = read_strategy()
    if not content.strip():
        print_status("尚未设定长期发展战略。在对话中与AI讨论你的人生/职业方向，AI会帮你逐步形成战略文档。")
        return
    print_markdown(content)


async def _handle_coping_command(runtime: OhMyRuntime, instruction: str) -> None:
    cleaned = instruction.strip()
    if not cleaned:
        content = read_coping()
        if not content.strip():
            print_status("尚未设定应对策略。使用 /coping <规则> 添加，格式：当 [状态] 时 → [应对措施]")
            return
        print_markdown(content)
        return
    rule = cleaned
    append_coping_rule("- " + rule if not rule.startswith("- ") else rule)
    print_success(f"应对规则已添加。使用 /coping 查看所有规则。")


async def run_repl(*, cwd: str, model: str | None, max_turns: int | None, base_url: str | None, system_prompt: str | None, api_key: str | None, api_format: str | None, permission_mode: str | None, active_profile: str | None) -> None:
    runtime = await build_runtime(
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        base_url=base_url,
        system_prompt=system_prompt,
        api_key=api_key,
        api_format=api_format,
        permission_mode=permission_mode,
        active_profile=active_profile,
        permission_prompt=_permission_prompt,
    )
    goal_context = GoalAgentContext()
    goal_context.refresh_goals()
    runtime.goal_context = goal_context

    await _maybe_daily_progress_update(runtime)

    transcript = _build_transcript_writer(runtime)

    scheduler = SchedulerService()
    runtime.engine.tool_metadata["scheduler"] = scheduler

    _register_os_shutdown_handler(scheduler)

    if scheduler.get_os_shutdown_pending():
        print_status("[Scheduled] System was shut down. Running pending shutdown tasks...")
        for task in scheduler.get_shutdown_tasks():
            print_status(f"[Scheduled/os-shutdown] {task.prompt[:80]}")
            await _stream_prompt(runtime, task.prompt, transcript)
            scheduler.mark_executed(task.id)
        scheduler.clear_os_shutdown_pending()

    settings = runtime.current_settings()
    profile_name, profile = settings.resolve_profile(runtime.settings_overrides.get("active_profile"))
    print_welcome(
        cwd=runtime.cwd,
        profile_name=profile_name,
        profile_label=profile.label,
        model=runtime.current_model(),
        permission_mode=settings.permission.mode,
        tool_count=len(_list_tools_data()),
        restored=None,
    )

    if not has_today_status():
        await _daily_status_review(runtime, transcript)

    while True:
        try:
            goal_topic = ""
            if goal_context.active_goal_id:
                active = goal_context.active_goal()
                if active:
                    goal_topic = active.topic
            line = await asyncio.to_thread(
                prompt_input,
                model_name=runtime.current_model(),
                plan_topics=_active_goal_topics(),
                goal_context=goal_context,
                goal_topic=goal_topic,
            )
        except EOFError:
            console().print()
            shutdown_tasks = scheduler.get_shutdown_tasks()
            for task in shutdown_tasks:
                print_status(f"[Scheduled/shutdown] {task.prompt[:80]}")
                await _stream_prompt(runtime, task.prompt, transcript)
                scheduler.mark_executed(task.id)
            break
        except KeyboardInterrupt:
            console().print()
            print_status("Use /exit to quit.")
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(GOAL_CYCLE_SENTINEL):
            await _handle_goal_cycle(runtime, transcript, stripped)
            continue
        if stripped == "/":
            print_help_panel()
            continue
        if stripped == "/exit":
            if not has_today_status():
                print_status("今日状态尚未记录。下次打开时请使用 /status 更新。")
            print_status("建议留出时间思考长期发展战略，下次打开时可与AI讨论。")
            if runtime.active_goal_id:
                await _maybe_prompt_memory_update_on_leave(runtime, transcript)
            else:
                await _maybe_prompt_normal_memory_update(runtime, transcript)
            shutdown_tasks = scheduler.get_shutdown_tasks()
            for task in shutdown_tasks:
                print_status(f"[Scheduled/shutdown] {task.prompt[:80]}")
                await _stream_prompt(runtime, task.prompt, transcript)
                scheduler.mark_executed(task.id)
            break
        if stripped == "/help":
            print_help_panel()
            continue
        if stripped == "/tools":
            print_tools_panel(_list_tools_data())
            continue
        if stripped == "/connect" or stripped.startswith("/connect "):
            _handle_connect_command(runtime, stripped[len("/connect") :].strip())
            continue
        if stripped == "/model" or stripped.startswith("/model "):
            _handle_model_command(runtime, stripped[len("/model") :].strip())
            continue
        if stripped == "/status" or stripped.startswith("/status "):
            instruction = stripped[len("/status") :].strip()
            await _handle_status_command(runtime, transcript, instruction)
            continue
        if stripped == "/sysinfo":
            print_status_panel(_runtime_status_rows(runtime))
            continue
        if stripped == "/strategy" or stripped.startswith("/strategy "):
            instruction = stripped[len("/strategy") :].strip()
            await _handle_strategy_command(runtime, instruction)
            continue
        if stripped == "/coping" or stripped.startswith("/coping "):
            instruction = stripped[len("/coping") :].strip()
            await _handle_coping_command(runtime, instruction)
            continue
        if stripped == "/restore" or stripped.startswith("/restore "):
            sid = stripped[len("/restore "):].strip() if stripped.startswith("/restore ") else None
            _handle_restore_command(runtime, transcript, session_id=sid)
            continue
        if stripped == "/sessions":
            _handle_sessions_list(runtime)
            continue
        if stripped.startswith("/user_profile"):
            instruction = stripped[len("/user_profile") :].strip()
            await _handle_user_profile_command(runtime, transcript, instruction)
            continue
        if stripped == "/exper" or stripped.startswith("/exper "):
            instruction = stripped[len("/exper") :].strip()
            await _handle_experience_command(runtime, transcript, instruction)
            continue
        if stripped == "/goal" or stripped.startswith("/goal "):
            instruction = stripped[len("/goal") :].strip()
            await _handle_goal_command(runtime, transcript, instruction)
            continue
        if stripped == "/plan" or stripped.startswith("/plan "):
            instruction = stripped[len("/plan") :].strip()
            await _handle_plan_command(runtime, transcript, instruction)
            continue
        if stripped == "/schedule" or stripped.startswith("/schedule "):
            instruction = stripped[len("/schedule") :].strip()
            _handle_schedule_command(scheduler, instruction)
            continue
        if stripped == "/clear":
            runtime.engine.clear()
            runtime.start_new_session()
            _sync_transcript_writer(runtime, transcript)
            runtime.refresh_system_prompt()
            console().print()
            print_status("Conversation cleared.")
            console().print()
            continue
        if stripped == "/continue":
            await _continue_pending(runtime, transcript)
            continue
        if stripped.startswith("/"):
            print_error(f"Unknown local command: {stripped}. Use / or /help to list commands.")
            print_help_panel()
            continue
        await _stream_prompt(runtime, stripped, transcript)


async def _handle_goal_cycle(runtime: OhMyRuntime, transcript: SessionTranscriptWriter, sentinel_line: str) -> None:
    goal_context = runtime.goal_context
    if goal_context is None:
        return
    if runtime.active_goal_id:
        await _maybe_prompt_memory_update_on_leave(runtime, transcript)
    else:
        await _maybe_prompt_normal_memory_update(runtime, transcript)
    _save_runtime_snapshot(runtime)
    new_goal_id = goal_context.active_goal_id
    runtime.active_goal_id = new_goal_id
    runtime.engine.tool_metadata["active_goal_id"] = new_goal_id
    if goal_context.previous_goal_id:
        saved_active = goal_context.active_goal_id
        goal_context.active_goal_id = goal_context.previous_goal_id
        goal_context.record_session_link(
            runtime.session_id,
            cwd=runtime.cwd,
            model=runtime.current_model(),
            message_count=len(runtime.engine.messages),
        )
        goal_context.active_goal_id = saved_active
    active_goal = goal_context.active_goal() if new_goal_id else None
    target_cwd, active_linked_dir, cwd_warning = _resolve_goal_runtime_cwd(
        runtime,
        active_goal.linked_dir if active_goal is not None else None,
    )
    runtime.set_cwd(target_cwd, linked_dir=active_linked_dir)
    runtime.engine.clear()
    runtime.start_new_session()
    runtime.engine.tool_metadata["active_goal_id"] = new_goal_id
    _sync_transcript_writer(runtime, transcript)
    runtime.refresh_system_prompt()
    _save_runtime_snapshot(runtime)
    if new_goal_id:
        topic = active_goal.topic if active_goal else ""
        print_goal_switch_feedback(topic)
    else:
        print_goal_switch_feedback(None)
    if cwd_warning:
        print_status(cwd_warning)


async def run_print_mode(*, prompt: str, cwd: str, model: str | None, max_turns: int | None, base_url: str | None, system_prompt: str | None, api_key: str | None, api_format: str | None, permission_mode: str | None, active_profile: str | None) -> None:
    runtime = await build_runtime(
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        base_url=base_url,
        system_prompt=system_prompt,
        api_key=api_key,
        api_format=api_format,
        permission_mode=permission_mode,
        active_profile=active_profile,
        permission_prompt=_permission_prompt,
    )
    transcript = _build_transcript_writer(runtime)
    await _stream_prompt_with_ui(runtime, prompt, transcript, decorated=False)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", callback=_version_callback, is_eager=True, help="Show version and exit."),
    print_mode: str | None = typer.Option(None, "--print", "-p", help="Submit one prompt and exit."),
    model: str | None = typer.Option(None, "--model", "-m", help="Model alias or full model id."),
    max_turns: int | None = typer.Option(None, "--max-turns", help="Maximum agent turns per prompt."),
    base_url: str | None = typer.Option(None, "--base-url", help="Provider base URL."),
    system_prompt: str | None = typer.Option(None, "--system-prompt", "-s", help="Override the default system prompt."),
    api_key: str | None = typer.Option(None, "--api-key", "-k", help="API key override."),
    api_format: str | None = typer.Option(None, "--api-format", help="API format, e.g. openai or anthropic."),
    permission_mode: str | None = typer.Option(None, "--permission-mode", help="Permission mode: default, plan, or full_auto."),
    profile: str | None = typer.Option(None, "--profile", help="Provider profile name."),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", hidden=True),
) -> None:
    del version
    if permission_mode is not None and permission_mode not in {mode.value for mode in PermissionMode}:
        raise typer.BadParameter("permission mode must be one of: default, plan, full_auto")
    if ctx.invoked_subcommand is not None:
        return
    if print_mode is not None:
        prompt = print_mode.strip()
        if not prompt:
            raise typer.BadParameter("--print requires a non-empty prompt")
        asyncio.run(
            run_print_mode(
                prompt=prompt,
                cwd=cwd,
                model=model,
                max_turns=max_turns,
                base_url=base_url,
                system_prompt=system_prompt,
                api_key=api_key,
                api_format=api_format,
                permission_mode=permission_mode,
                active_profile=profile,
            )
        )
        return
    asyncio.run(
        run_repl(
            cwd=cwd,
            model=model,
            max_turns=max_turns,
            base_url=base_url,
            system_prompt=system_prompt,
            api_key=api_key,
            api_format=api_format,
            permission_mode=permission_mode,
            active_profile=profile,
        )
    )
