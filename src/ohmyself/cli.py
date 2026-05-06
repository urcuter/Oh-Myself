from __future__ import annotations

import asyncio
import json
import shlex
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
from ohmyself.runtime import OhMyRuntime, build_runtime
from ohmyself.services import (
    MAX_ACTIVE_GOALS,
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
    load_latest_session_snapshot,
    read_plan_inbox,
    read_today_plan,
    save_session_snapshot,
    save_user_profile,
    stop_goal,
    update_goal_progress,
)
from ohmyself.terminal_ui import (
    RestoredSessionSummary,
    console,
    make_live_markdown,
    update_live_markdown,
    print_error,
    print_help_panel,
    prompt_permission,
    print_status,
    print_context_snapshot,
    print_status_panel,
    print_tool_completed,
    print_tool_started,
    print_welcome,
    print_tools_panel,
    prompt_input,
    print_markdown,
    print_subagent_completed,
    print_subagent_started,
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
    save_session_snapshot(
        cwd=runtime.cwd,
        model=runtime.engine.model,
        messages=runtime.engine.messages,
        usage=runtime.engine.total_usage,
        session_id=runtime.session_id,
        session_started_at=runtime.session_started_at.isoformat(),
        tool_metadata=runtime.engine.tool_metadata,
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


def _handle_restore_command(runtime: OhMyRuntime, transcript: SessionTranscriptWriter) -> None:
    restored_summary = _restore_latest_session(runtime)
    if restored_summary is None:
        message = "No saved session is available for this workspace."
        print_status(message)
        transcript.record_status("Session", message)
        return
    _sync_transcript_writer(runtime, transcript)
    message = f"Restored session {restored_summary.session_id} with {restored_summary.message_count} messages."
    print_status(message)
    transcript.record_status("Session", message)


async def _stream_prompt(runtime: OhMyRuntime, prompt: str, transcript: SessionTranscriptWriter) -> None:
    await _stream_prompt_with_ui(runtime, prompt, transcript, decorated=True)


async def _stream_prompt_with_ui(runtime: OhMyRuntime, prompt: str, transcript: SessionTranscriptWriter, *, decorated: bool) -> None:
    runtime.refresh_system_prompt(prompt)
    _sync_transcript_writer(runtime, transcript)
    transcript.record_user_prompt(prompt)

    output_started = False
    after_tool = False  # blank line needed before next assistant text
    assistant_buffer = ""
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
                    assistant_buffer += event.text
                    if live is None:
                        if after_tool:
                            console().print()
                            after_tool = False
                        live = make_live_markdown(assistant_buffer)
                        live.start()  # type: ignore[union-attr]
                    else:
                        update_live_markdown(live, assistant_buffer)  # type: ignore[union-attr]

            elif isinstance(event, AssistantTurnComplete):
                if not output_started and event.message.text.strip():
                    console().print()
                    output_started = True
                if live is not None:
                    update_live_markdown(live, event.message.text or assistant_buffer)  # type: ignore[union-attr]
                    _stop_live()
                elif event.message.text.strip():
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


def _runtime_status_rows(runtime: OhMyRuntime) -> list[tuple[str, str]]:
    settings = runtime.current_settings()
    profile_name, profile = settings.resolve_profile(runtime.settings_overrides.get("active_profile"))
    configured = AuthManager(settings).get_profile_statuses()[profile_name]["configured"]
    return [
        ("config", str(get_settings_path())),
        ("home", str(get_home_dir())),
        ("profile", profile_name),
        ("provider", profile.provider),
        ("model", profile.resolved_model),
        ("auth", str(configured)),
        ("permission", settings.permission.mode),
        ("tools", str(len(_list_tools_data()))),
        ("workspace", runtime.cwd),
        ("session", runtime.session_id),
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
    return build_plan_organize_prompt(
        goal_context=goal_context,
        active_goal_count=len(active_goals),
        goal_limit=MAX_ACTIVE_GOALS,
    )


async def _continue_pending(runtime: OhMyRuntime, transcript: SessionTranscriptWriter) -> None:
    if not runtime.engine.has_pending_continuation():
        print_status("No paused tool loop is waiting for continuation.")
        return

    output_started = False
    after_tool = False
    assistant_buffer = ""
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
                    assistant_buffer += event.text
                    if live is None:
                        if after_tool:
                            console().print()
                            after_tool = False
                        live = make_live_markdown(assistant_buffer)
                        live.start()  # type: ignore[union-attr]
                    else:
                        update_live_markdown(live, assistant_buffer)  # type: ignore[union-attr]

            elif isinstance(event, AssistantTurnComplete):
                if not output_started and event.message.text.strip():
                    console().print()
                    output_started = True
                if live is not None:
                    update_live_markdown(live, event.message.text or assistant_buffer)  # type: ignore[union-attr]
                    _stop_live()
                elif event.message.text.strip():
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


async def _retrieve_experience_matches(runtime: OhMyRuntime, transcript: SessionTranscriptWriter, question: str) -> str | None:
    runtime.refresh_system_prompt(question)
    registry = runtime.engine.tool_metadata.get("tool_registry")
    if not isinstance(registry, ToolRegistry):
        print_error("Experience retrieval is unavailable: tool registry is missing.")
        return None
    tool = registry.get("delegate_task")
    if not isinstance(tool, DelegateTaskTool):
        print_error("Experience retrieval is unavailable: delegate_task tool is missing.")
        return None

    task = build_experience_retrieval_task(question)
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
            entry = append_experience(content)
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
    retrieval_report = await _retrieve_experience_matches(runtime, transcript, cleaned)
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


async def _handle_goal_command(
    runtime: OhMyRuntime,
    transcript: SessionTranscriptWriter,
    instruction: str,
) -> None:
    del runtime
    cleaned = instruction.strip()

    if not cleaned:
        print_markdown(format_goals_markdown())
        return

    if cleaned.startswith("progress "):
        parts = cleaned.split()
        if len(parts) != 3:
            print_error("Usage: /goal progress [id] [0-100]")
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
        topic, description, ends_at, progress_percent = _parse_goal_add_arguments(cleaned)
        entry = append_goal(topic, description=description, ends_at=ends_at, progress_percent=progress_percent)
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


def _parse_goal_add_arguments(raw: str) -> tuple[str, str, date | None, int]:
    tokens = shlex.split(raw)
    args = tokens[1:] if tokens and tokens[0] == "add" else tokens
    topic_parts: list[str] = []
    description = ""
    ends_at: date | None = None
    progress_percent = 0
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
    return topic, description, ends_at, progress_percent


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
    transcript = _build_transcript_writer(runtime)
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
    while True:
        try:
            line = await asyncio.to_thread(
                prompt_input,
                model_name=runtime.current_model(),
                plan_topics=_active_goal_topics(),
            )
        except EOFError:
            console().print()
            break
        except KeyboardInterrupt:
            console().print()
            print_status("Use /exit to quit.")
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "/":
            print_help_panel()
            continue
        if stripped == "/exit":
            break
        if stripped == "/help":
            print_help_panel()
            continue
        if stripped == "/tools":
            print_tools_panel(_list_tools_data())
            continue
        if stripped == "/status":
            print_status_panel(_runtime_status_rows(runtime))
            continue
        if stripped == "/restore":
            _handle_restore_command(runtime, transcript)
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
        if stripped == "/clear":
            runtime.engine.clear()
            runtime.start_new_session()
            _sync_transcript_writer(runtime, transcript)
            console().print()
            settings = runtime.current_settings()
            profile_name, profile = settings.resolve_profile(runtime.settings_overrides.get("active_profile"))
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
