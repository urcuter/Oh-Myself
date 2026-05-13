from __future__ import annotations

from pathlib import Path


def resolve_tool_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def resolve_workspace_path(base: Path, candidate: str, extra_roots: list[Path] | None = None) -> Path:
    workspace = base.resolve()
    path = resolve_tool_path(workspace, candidate)
    try:
        path.relative_to(workspace)
        return path
    except ValueError:
        pass
    if extra_roots:
        for root in extra_roots:
            try:
                path.relative_to(root.resolve())
                return path
            except ValueError:
                pass
    raise ValueError(f"Path is outside the workspace: {path}")
