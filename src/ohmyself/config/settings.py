"""Settings and provider profiles for Oh Myself."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from ohmyself.config.paths import get_settings_path


class PathRuleConfig(BaseModel):
    pattern: str
    allow: bool = True


class PermissionSettings(BaseModel):
    mode: str = "default"
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    path_rules: list[PathRuleConfig] = Field(default_factory=list)
    denied_commands: list[str] = Field(default_factory=list)


class ProviderProfile(BaseModel):
    label: str
    provider: str
    api_format: str
    default_model: str
    base_url: str | None = None
    last_model: str | None = None
    model_history: list[str] = Field(default_factory=list)

    @property
    def resolved_model(self) -> str:
        return (self.last_model or "").strip() or self.default_model


def default_provider_profiles() -> dict[str, ProviderProfile]:
    return {
        "openai-compatible": ProviderProfile(
            label="OpenAI-Compatible API",
            provider="openai",
            api_format="openai",
            default_model="gpt-5.4",
        ),
        "claude-api": ProviderProfile(
            label="Anthropic-Compatible API",
            provider="anthropic",
            api_format="anthropic",
            default_model="claude-sonnet-4-6",
        ),
    }


class Settings(BaseModel):
    active_profile: str = "openai-compatible"
    profiles: dict[str, ProviderProfile] = Field(default_factory=default_provider_profiles)
    max_tokens: int = 16384
    timeout: float = 30.0
    max_turns: int = 200
    system_prompt: str | None = None
    permission: PermissionSettings = Field(default_factory=PermissionSettings)
    verbose: bool = False
    effort: str = "medium"
    passes: int = 1

    def merged_profiles(self) -> dict[str, ProviderProfile]:
        merged = default_provider_profiles()
        for name, profile in self.profiles.items():
            merged[name] = (
                profile
                if isinstance(profile, ProviderProfile)
                else ProviderProfile.model_validate(profile)
            )
        return merged

    def resolve_profile(self, name: str | None = None) -> tuple[str, ProviderProfile]:
        profiles = self.merged_profiles()
        profile_name = (name or self.active_profile or "openai-compatible").strip()
        if profile_name not in profiles:
            profile_name = "openai-compatible"
        return profile_name, profiles[profile_name]


def _apply_env_overrides(settings: Settings) -> Settings:
    profile_name, profile = settings.resolve_profile()
    updates: dict[str, object] = {}

    active_updates: dict[str, object] = {}
    base_url = os.environ.get("OHMY_BASE_URL", "").strip()
    if not base_url:
        if profile.provider == "openai":
            base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
        elif profile.provider == "anthropic":
            base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        active_updates["base_url"] = base_url

    model = os.environ.get("OHMY_MODEL", "").strip()
    if model:
        active_updates["last_model"] = model

    if active_updates:
        profiles = settings.merged_profiles()
        profiles[profile_name] = profile.model_copy(update=active_updates)
        updates["profiles"] = profiles

    active_profile = os.environ.get("OHMY_PROFILE", "").strip()
    if active_profile:
        updates["active_profile"] = active_profile

    max_turns = os.environ.get("OHMY_MAX_TURNS", "").strip()
    if max_turns:
        updates["max_turns"] = int(max_turns)

    timeout = os.environ.get("OHMY_TIMEOUT", "").strip()
    if timeout:
        updates["timeout"] = float(timeout)

    if not updates:
        return settings
    return settings.model_copy(update=updates)


def load_settings(path: Path | None = None) -> Settings:
    settings_path = path or get_settings_path()
    if settings_path.exists():
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        settings = Settings.model_validate(raw)
    else:
        settings = Settings()
    return _apply_env_overrides(settings)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    settings_path = path or get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        settings.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

