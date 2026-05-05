from __future__ import annotations

import asyncio
import json
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
    SessionTranscriptWriter,
    generate_user_profile,
    load_latest_session_snapshot,
    save_session_snapshot,
    save_user_profile,
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
    print_status_panel,
    print_tool_completed,
    print_tool_started,
    print_welcome,
    print_tools_panel,
    prompt_text,
    print_markdown,
    print_subagent_completed,
    print_subagent_started,
)
from ohmyself.tools import create_tool_registry

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
    restored_summary = _restore_latest_session(runtime)
    if restored_summary:
        _sync_transcript_writer(runtime, transcript)
    settings = runtime.current_settings()
    profile_name, profile = settings.resolve_profile(runtime.settings_overrides.get("active_profile"))
    print_welcome(
        cwd=runtime.cwd,
        profile_name=profile_name,
        profile_label=profile.label,
        model=runtime.current_model(),
        permission_mode=settings.permission.mode,
        tool_count=len(_list_tools_data()),
        restored=restored_summary,
    )
    while True:
        try:
            line = await asyncio.to_thread(
                console().input,
                prompt_text(model_name=runtime.current_model()),
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
        if stripped.startswith("/user_profile"):
            instruction = stripped[len("/user_profile") :].strip()
            await _handle_user_profile_command(runtime, transcript, instruction)
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
