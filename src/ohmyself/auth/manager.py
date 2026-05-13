"""Authentication and provider-profile management."""

from __future__ import annotations

import os

from ohmyself.auth.storage import clear_slot, load_api_key, store_api_key
from ohmyself.config import ProviderProfile, Settings, load_settings, save_settings


def _credential_env_var(provider: str) -> str:
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    return "OPENAI_API_KEY"


class AuthManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()

    @property
    def settings(self) -> Settings:
        return self._settings

    def save_settings(self) -> None:
        save_settings(self.settings)

    def list_profiles(self) -> dict[str, ProviderProfile]:
        return self.settings.merged_profiles()

    def get_active_profile(self) -> str:
        return self.settings.resolve_profile()[0]

    def use_profile(self, name: str) -> None:
        profiles = self.list_profiles()
        if name not in profiles:
            raise ValueError(f"Unknown provider profile: {name!r}")
        self._settings = self.settings.model_copy(update={"active_profile": name})
        self.save_settings()

    def upsert_profile(self, name: str, profile: ProviderProfile) -> None:
        profiles = self.list_profiles()
        profiles[name] = profile
        self._settings = self.settings.model_copy(update={"profiles": profiles})
        self.save_settings()

    def update_profile(
        self,
        name: str,
        *,
        label: str | None = None,
        provider: str | None = None,
        api_format: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        profiles = self.list_profiles()
        if name not in profiles:
            raise ValueError(f"Unknown provider profile: {name!r}")
        current = profiles[name]
        profiles[name] = current.model_copy(
            update={
                "label": label or current.label,
                "provider": provider or current.provider,
                "api_format": api_format or current.api_format,
                "default_model": model or current.default_model,
                "last_model": model or current.last_model,
                "base_url": base_url if base_url is not None else current.base_url,
            }
        )
        self._settings = self.settings.model_copy(update={"profiles": profiles})
        self.save_settings()

    def update_profile_history(self, name: str, history: list[str]) -> None:
        profiles = self.list_profiles()
        if name not in profiles:
            raise ValueError(f"Unknown provider profile: {name!r}")
        current = profiles[name]
        profiles[name] = current.model_copy(update={"model_history": history})
        self._settings = self.settings.model_copy(update={"profiles": profiles})
        self.save_settings()

    def remove_profile(self, name: str) -> None:
        profiles = self.list_profiles()
        if name not in profiles:
            raise ValueError(f"Unknown provider profile: {name!r}")
        if name == self.get_active_profile():
            raise ValueError("Cannot remove the active profile.")
        del profiles[name]
        self._settings = self.settings.model_copy(update={"profiles": profiles})
        self.save_settings()

    def _credential_slot(self, profile_name: str) -> str:
        return f"profile:{profile_name}"

    def store_profile_credential(self, profile_name: str, api_key: str) -> None:
        store_api_key(self._credential_slot(profile_name), api_key)

    def clear_profile_credential(self, profile_name: str) -> None:
        clear_slot(self._credential_slot(profile_name))

    def resolve_api_key(self, profile_name: str | None = None) -> str:
        resolved_name, profile = self.settings.resolve_profile(profile_name)
        env_var = _credential_env_var(profile.provider)
        env_value = os.environ.get(env_var, "").strip()
        if env_value:
            return env_value
        stored = load_api_key(self._credential_slot(resolved_name))
        if stored:
            return stored
        raise ValueError(
            f"No API key configured for profile '{resolved_name}'. "
            f"Set {env_var} or run `ohmy auth login {resolved_name}`."
        )

    def get_profile_statuses(self) -> dict[str, dict[str, object]]:
        active = self.get_active_profile()
        statuses: dict[str, dict[str, object]] = {}
        for name, profile in self.list_profiles().items():
            env_var = _credential_env_var(profile.provider)
            env_value = os.environ.get(env_var, "").strip()
            stored = load_api_key(self._credential_slot(name))
            statuses[name] = {
                "label": profile.label,
                "provider": profile.provider,
                "api_format": profile.api_format,
                "model": profile.resolved_model,
                "base_url": profile.base_url,
                "configured": bool(env_value or stored),
                "auth_source": env_var if env_value else ("stored" if stored else "missing"),
                "active": name == active,
            }
        return statuses

