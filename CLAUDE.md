# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Oh Myself is a standalone terminal AI agent with local tool use, built on the [OpenHarness](https://github.com/HKUDS/OpenHarness) agent framework. The CLI binary is `ohmy`.

## Commands

```bash
# Install (editable)
pip install -e .

# Run interactively
ohmy

# Run a single prompt
ohmy -p "inspect this repository"

# Run all tests
pytest -q

# Run a single test
pytest tests/test_cli.py -k test_switch_model

# Run a single test with verbose output
pytest tests/test_terminal_ui.py::test_function_name -v
```

## Architecture

### Entry point
`src/ohmyself/cli.py` — ~95KB Typer CLI with the full interactive REPL. App entry: `ohmyself.cli:app`. Contains slash commands (`/goal`, `/exper`, `/model`, `/connect`, `/plan`, `/status`, `/schedule`, etc.), goal-agent context switching, session management, and experience library operations.

### Runtime (`src/ohmyself/runtime.py`)
`OhMyRuntime` is the central orchestrator that wires together the engine, API client, permission checker, tool registry, and settings. Created via `build_runtime()`. Manages session lifecycle (`start_new_session`, `restore_session_snapshot`), model switching, reconfigure (base_url/key/model/effort), and system prompt refresh. Goal agent context (active goal, linked dir) is attached to the runtime via `active_goal_id` and `goal_context`.

### Engine layer (`src/ohmyself/engine/`)
- `query_engine.py` — `QueryEngine` holds the API client, tool registry, permission checker, messages, and cost tracker. Orchestrates the agent loop.
- `query.py` — `run_query()` streams responses from the model, dispatches tool calls through the permission checker, and yields `StreamEvent`s.
- `messages.py` — `ConversationMessage` and `ToolResultBlock` dataclasses (Pydantic models).
- `stream_events.py` — Stream event types: `AssistantTextDelta`, `ToolExecutionStarted/Completed`, `SubagentStarted/Completed`, etc.

### API layer (`src/ohmyself/api/`)
- `client.py` — Protocol `SupportsStreamingMessages` defining `stream_message(ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]`.
- `anthropic_client.py` — `AnthropicApiClient` implementing the Anthropic-compatible streaming API.
- `openai_client.py` — `OpenAICompatibleClient` implementing the OpenAI-compatible streaming API.
- `usage.py` / `errors.py` — Usage snapshot and error types.

### Tools (`src/ohmyself/tools/`)
11 tools registered via `create_tool_registry()`: `BashTool`, `FileReadTool`, `FileWriteTool`, `FileEditTool`, `GlobTool`, `GrepTool`, `DelegateTaskTool` (subagent spawning), `TodoWriteTool`, `ScheduleTaskTool`, `ToolSearchTool`, `UpdateUserStatusTool`. All tools extend `BaseTool` with Pydantic input models and async `execute()`.

### Permissions (`src/ohmyself/permissions/`)
- `modes.py` — `PermissionMode` enum: `default` (ask before writes), `auto` (allow all), `plan` (block writes), `accept_edits`.
- `checker.py` — `PermissionChecker` with path-level rules, sensitive-path patterns (`.ssh`, `.aws`, credentials), and user-protected file guarding (`coping.md`, `strategy.md` under `~/.ohmyself/`). Returns `PermissionDecision(allowed, requires_confirmation, reason)`.

### Config (`src/ohmyself/config/`)
- `settings.py` — Pydantic `Settings` with provider profiles, permission config, model/turns/effort. `load_settings()` reads `~/.ohmyself/settings.json` and applies env var overrides (`OHMY_MODEL`, `OHMY_BASE_URL`, `OPENAI_BASE_URL`, etc.).
- `paths.py` — Home directory and settings file paths.

### Auth (`src/ohmyself/auth/`)
`AuthManager` handles API key storage in `~/.ohmyself/credentials.json` and profile credential management.

### Services (`src/ohmyself/services/`)
Domain logic layer with independent modules:
- `goal.py` — CRUD for goals, active goal limit (MAX_ACTIVE_GOALS=5), progress tracking
- `goal_memory.py` — Per-goal persistent memory (ai_notes.md, user_prefs.md, context.md)
- `goal_agent.py` — `GoalAgentContext` for goal-specific agent sessions with isolated memory
- `goal_progress.py` — Daily goal progress assessment
- `goal_session.py` — Links CLI sessions to goals
- `plan.py` — Daily plan management (inbox + today plan files)
- `experience.py` — Life experience library (~/.ohmyself/experiences/)
- `status.py` — Daily status tracking with structured fields
- `scheduler.py` — Scheduled task management (cron-based, durable)
- `coping.py` / `strategy.py` — User-protected reference files
- `session_storage.py` — Session snapshot save/load
- `transcript_memory.py` — Session transcript writer
- `user_profile.py` — User profile generation and storage

### Prompts (`src/ohmyself/prompts/`)
- `system_prompt.py` — Assembles the Chinese-language system prompt from foundation + environment info + user profile + CLAUDE.md + memory injection.
- `environment.py` — Platform/environment info for the system prompt.

### Terminal UI (`src/ohmyself/terminal_ui.py`)
Rich-based TUI with custom color scheme, slash-command completer, inline markdown rendering, and async event loop integration. Defines ~40 local commands.

### Protected files
`coping.md` and `strategy.md` in the data directory (`~/.ohmyself/`) are user-maintained reference files. The agent may read them but must not modify or create them without explicit user instruction. The `PermissionChecker` enforces this.

## Testing

Tests are in `tests/` (top-level) and `src/ohmyself/tests/` (internal). Use pytest directly:
```bash
pytest -q                          # all tests
pytest tests/test_cli.py           # CLI tests
pytest src/ohmyself/tests/         # internal unit tests
```
