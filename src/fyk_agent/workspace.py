from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkspaceError(ValueError):
    """Raised when a requested path violates the workspace boundary."""


_PRIVATE_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}
_INTERNAL_PARTS = {".git", ".yukai", ".fyk-agent"}


@dataclass(frozen=True)
class Workspace:
    root: Path

    def __init__(self, root: Path):
        resolved = root.expanduser().resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {resolved}")
        object.__setattr__(self, "root", resolved)

    def resolve(
        self,
        user_path: str,
        *,
        must_exist: bool = False,
        for_write: bool = False,
        allow_internal: bool = False,
    ) -> Path:
        if not isinstance(user_path, str) or not user_path.strip():
            raise WorkspaceError("Path must be a non-empty string")
        if "\x00" in user_path:
            raise WorkspaceError("Path contains a null byte")
        supplied = Path(user_path)
        if supplied.is_absolute():
            raise WorkspaceError("Absolute paths are not allowed; use a workspace-relative path")

        candidate = (self.root / supplied).resolve(strict=False)
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(f"Path escapes the workspace: {user_path}") from exc

        lowered = {part.lower() for part in relative.parts}
        if not allow_internal and lowered & _INTERNAL_PARTS:
            raise WorkspaceError("Internal agent and Git metadata paths are not accessible")
        if any(_is_private_name(part) for part in relative.parts):
            raise WorkspaceError("Credential-like files are not accessible to the agent")
        if for_write and candidate == self.root:
            raise WorkspaceError("The workspace root itself cannot be overwritten")
        if must_exist and not candidate.exists():
            raise WorkspaceError(f"Path does not exist: {relative.as_posix()}")
        return candidate

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix() or "."


def _is_private_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in _PRIVATE_NAMES or lowered.startswith(".env.") or lowered.endswith(".pem")
