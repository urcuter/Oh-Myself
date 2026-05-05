from __future__ import annotations

import asyncio
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ohmyself.api.client import SupportsStreamingMessages
from ohmyself.auth import AuthManager
from ohmyself.config import PathRuleConfig, load_settings
from ohmyself.engine.messages import ConversationMessage
from ohmyself.engine.query_engine import QueryEngine
from ohmyself.permissions import PermissionChecker, PermissionMode
from ohmyself.prompts.system_prompt import build_system_prompt
from ohmyself.tools import create_tool_registry

PermissionPrompt = Callable[[str, str], Awaitable[bool]]


@dataclass
class OhMyRuntime:
    engine: QueryEngine
    cwd: str
    settings_overrides: dict[str, Any]
    session_id: str
    session_started_at: datetime

    def current_settings(self):
        settings = load_settings()
        return _apply_settings_overrides(settings, self.settings_overrides)

    def refresh_system_prompt(self, latest_user_prompt: str | None = None) -> None:
        del latest_user_prompt
        settings = self.current_settings()
        profile_name, profile = settings.resolve_profile()
        self.engine.set_model(profile.resolved_model)
        self.engine.set_system_prompt(build_system_prompt(settings.system_prompt, cwd=self.cwd))

    def current_model(self) -> str:
        settings = self.current_settings()
        _, profile = settings.resolve_profile()
        return profile.resolved_model

    def start_new_session(self) -> str:
        self.session_id = uuid4().hex[:12]
        self.session_started_at = datetime.now().astimezone()
        self.engine.tool_metadata["session_id"] = self.session_id
        self.engine.tool_metadata["agent_depth"] = 0
        self.engine.tool_metadata["agent_lineage"] = [self.session_id]
        self.engine.tool_metadata["subagent_runs"] = []
        self.engine.tool_metadata["subagent_semaphore"] = asyncio.Semaphore(2)
        return self.session_id

    def restore_session_snapshot(self, snapshot: dict[str, Any]) -> None:
        messages = snapshot.get("messages", [])
        if isinstance(messages, list):
            normalized = [
                message if isinstance(message, ConversationMessage) else ConversationMessage.model_validate(message)
                for message in messages
            ]
            self.engine.load_messages(normalized)
        session_id = str(snapshot.get("session_id") or self.session_id)
        self.session_id = session_id
        self.engine.tool_metadata["session_id"] = session_id
        restored_tool_metadata = snapshot.get("tool_metadata")
        if isinstance(restored_tool_metadata, dict):
            for key in ("active_profile", "active_artifacts", "last_goal", "subagent_runs", "agent_depth", "agent_lineage"):
                if key in restored_tool_metadata:
                    self.engine.tool_metadata[key] = restored_tool_metadata[key]
        self.engine.tool_metadata.setdefault("agent_depth", 0)
        self.engine.tool_metadata.setdefault("agent_lineage", [session_id])
        self.engine.tool_metadata.setdefault("subagent_runs", [])
        self.engine.tool_metadata.setdefault("subagent_semaphore", asyncio.Semaphore(2))
        started_at = snapshot.get("session_started_at")
        if isinstance(started_at, str) and started_at.strip():
            try:
                self.session_started_at = datetime.fromisoformat(started_at)
            except ValueError:
                self.session_started_at = datetime.now().astimezone()
        if self.settings_overrides.get("model") is None:
            restored_model = snapshot.get("model")
            if isinstance(restored_model, str) and restored_model.strip():
                self.settings_overrides["model"] = restored_model
        self.refresh_system_prompt()


def _protected_paths() -> list[PathRuleConfig]:
    from ohmyself.config.paths import get_credentials_path

    return [PathRuleConfig(pattern=str(get_credentials_path().resolve()), allow=False)]


def _apply_settings_overrides(settings, overrides: dict[str, Any]):
    profile_name, profile = settings.resolve_profile(overrides.get("active_profile"))
    profile_updates: dict[str, Any] = {}
    if overrides.get("model") is not None:
        profile_updates["last_model"] = overrides["model"]
    if overrides.get("base_url") is not None:
        profile_updates["base_url"] = overrides["base_url"]
    if overrides.get("api_format") is not None:
        profile_updates["api_format"] = overrides["api_format"]
    if profile_updates:
        profiles = settings.merged_profiles()
        profiles[profile_name] = profile.model_copy(update=profile_updates)
        settings = settings.model_copy(update={"profiles": profiles, "active_profile": profile_name})
    scalar_updates = {key: value for key, value in overrides.items() if key in {"system_prompt", "max_turns"} and value is not None}
    if scalar_updates:
        settings = settings.model_copy(update=scalar_updates)
    permission_mode = overrides.get("permission_mode")
    if permission_mode is not None:
        permission = settings.permission.model_copy(update={"mode": PermissionMode(permission_mode).value})
        settings = settings.model_copy(update={"permission": permission})
    permission = settings.permission.model_copy(update={"path_rules": [*settings.permission.path_rules, *_protected_paths()]})
    return settings.model_copy(update={"permission": permission})


def _build_api_client(settings, *, active_profile: str | None = None, api_key: str | None = None) -> SupportsStreamingMessages:
    from ohmyself.api.anthropic_client import AnthropicApiClient
    from ohmyself.api.openai_client import OpenAICompatibleClient

    profile_name, profile = settings.resolve_profile(active_profile)
    key = api_key or AuthManager(settings).resolve_api_key(profile_name)
    if profile.api_format == "anthropic":
        return AnthropicApiClient(api_key=key, base_url=profile.base_url)
    return OpenAICompatibleClient(api_key=key, base_url=profile.base_url, timeout=settings.timeout)


async def build_runtime(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    active_profile: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    permission_prompt: PermissionPrompt | None = None,
    permission_mode: str | None = None,
) -> OhMyRuntime:
    overrides = {
        "model": model,
        "max_turns": max_turns,
        "base_url": base_url,
        "system_prompt": system_prompt,
        "api_format": api_format,
        "active_profile": active_profile,
        "permission_mode": permission_mode,
    }
    settings = _apply_settings_overrides(load_settings(), overrides)
    resolved_cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    resolved_api_client = api_client or _build_api_client(settings, active_profile=active_profile, api_key=api_key)
    profile_name, profile = settings.resolve_profile(active_profile)
    tool_registry = create_tool_registry()
    session_id = uuid4().hex[:12]
    session_started_at = datetime.now().astimezone()
    engine = QueryEngine(
        api_client=resolved_api_client,
        tool_registry=tool_registry,
        permission_checker=PermissionChecker(settings.permission),
        cwd=resolved_cwd,
        model=profile.resolved_model,
        system_prompt=build_system_prompt(settings.system_prompt, cwd=resolved_cwd),
        max_tokens=settings.max_tokens,
        max_turns=settings.max_turns,
        permission_prompt=permission_prompt,
        tool_metadata={
            "session_id": session_id,
            "tool_registry": tool_registry,
            "active_profile": profile_name,
            "active_artifacts": [],
            "agent_depth": 0,
            "agent_lineage": [session_id],
            "subagent_runs": [],
            "subagent_semaphore": asyncio.Semaphore(2),
        },
    )
    return OhMyRuntime(
        engine=engine,
        cwd=resolved_cwd,
        settings_overrides=overrides,
        session_id=session_id,
        session_started_at=session_started_at,
    )
