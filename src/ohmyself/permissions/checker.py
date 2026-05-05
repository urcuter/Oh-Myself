from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from ohmyself.config.settings import PermissionSettings
from ohmyself.permissions.modes import PermissionMode

SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    "*/.ssh/*",
    "*/.aws/credentials",
    "*/.aws/config",
    "*/.kube/config",
    "*/.ohmyself/credentials.json",
)


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""


class PermissionChecker:
    def __init__(self, settings: PermissionSettings) -> None:
        self._settings = settings

    def with_mode(self, mode: PermissionMode | str) -> "PermissionChecker":
        resolved = PermissionMode(mode).value
        return PermissionChecker(self._settings.model_copy(update={"mode": resolved}))

    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
    ) -> PermissionDecision:
        if file_path:
            for pattern in SENSITIVE_PATH_PATTERNS:
                if fnmatch.fnmatch(file_path.rstrip("/"), pattern) or fnmatch.fnmatch(file_path.rstrip("/") + "/", pattern):
                    return PermissionDecision(False, reason=f"Access denied: sensitive path {file_path}")
        if tool_name in self._settings.denied_tools:
            return PermissionDecision(False, reason=f"{tool_name} is explicitly denied")
        if tool_name in self._settings.allowed_tools:
            return PermissionDecision(True, reason=f"{tool_name} is explicitly allowed")
        for rule in self._settings.path_rules:
            if file_path and fnmatch.fnmatch(file_path, rule.pattern):
                if not rule.allow:
                    return PermissionDecision(False, reason=f"Path {file_path} matches deny rule: {rule.pattern}")
        if command:
            for pattern in self._settings.denied_commands:
                if fnmatch.fnmatch(command, pattern):
                    return PermissionDecision(False, reason=f"Command matches deny pattern: {pattern}")
        mode = PermissionMode(self._settings.mode)
        if mode == PermissionMode.FULL_AUTO:
            return PermissionDecision(True, reason="full_auto allows all tools")
        if is_read_only:
            return PermissionDecision(True, reason="read-only tools are allowed")
        if mode == PermissionMode.PLAN:
            return PermissionDecision(False, reason="Plan mode blocks mutating tools.")
        return PermissionDecision(
            False,
            requires_confirmation=True,
            reason="Mutating tools require user confirmation in default mode.",
        )
