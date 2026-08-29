from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable

from .events import EventLog
from .journal import ChangeJournal
from .workspace import Workspace, WorkspaceError


MAX_READ_BYTES = 256_000
MAX_COMMAND_OUTPUT = 40_000
MAX_SEARCH_FILE_BYTES = 1_000_000


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    changes_state: bool = False

    def api_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(
        self,
        workspace: Workspace,
        approve: Callable[[str, dict[str, Any]], bool] | None = None,
    ):
        self.workspace = workspace
        self.journal = ChangeJournal(workspace.root)
        self.events = EventLog(workspace.root)
        self.approve = approve or (lambda _name, _arguments: True)
        self._specs = self._build_specs()

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [spec.api_schema() for spec in self._specs.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        spec = self._specs.get(name)
        if spec is None:
            result = _failure(f"Unknown tool: {name}", error_type="unknown_tool")
            self.events.emit("tool_error", tool=name, error=result["error"])
            return result
        if not isinstance(arguments, dict):
            return _failure("Tool arguments must be a JSON object", error_type="invalid_arguments")
        if spec.changes_state and not self.approve(name, arguments):
            result = _failure("User rejected this operation", error_type="rejected")
            self.events.emit("tool_rejected", tool=name)
            return result

        try:
            result = spec.handler(**arguments)
        except TypeError as exc:
            result = _failure(f"Invalid arguments: {exc}", error_type="invalid_arguments")
        except (ToolError, WorkspaceError, OSError, UnicodeError) as exc:
            result = _failure(str(exc), error_type=type(exc).__name__)
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        self.events.emit(
            "tool_finished",
            tool=name,
            ok=result.get("ok", False),
            duration_ms=duration_ms,
            summary=_result_summary(result),
        )
        result["duration_ms"] = duration_ms
        return result

    def undo_last(self) -> dict[str, Any]:
        snapshot = self.journal.undo_last()
        if snapshot is None:
            return _failure("No file change is available to undo", error_type="nothing_to_undo")
        self.events.emit("undo", path=snapshot.relative_path, snapshot_id=snapshot.snapshot_id)
        return {
            "ok": True,
            "path": snapshot.relative_path,
            "snapshot_id": snapshot.snapshot_id,
            "restored_previous_file": snapshot.existed,
        }

    def _build_specs(self) -> dict[str, ToolSpec]:
        specs = [
            ToolSpec(
                "list_files",
                "List files and directories inside the workspace. Use this before assuming project structure.",
                _object_schema(
                    {
                        "path": {"type": "string", "description": "Relative directory; default is ."},
                        "pattern": {"type": "string", "description": "Optional glob matched against relative paths"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
                    }
                ),
                self.list_files,
            ),
            ToolSpec(
                "read_file",
                "Read a UTF-8 text file with line numbers. Use offset and limit for large files.",
                _object_schema(
                    {
                        "path": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                    },
                    required=["path"],
                ),
                self.read_file,
            ),
            ToolSpec(
                "search_text",
                "Search UTF-8 project files for a literal string and return matching lines.",
                _object_schema(
                    {
                        "query": {"type": "string", "minLength": 1},
                        "path": {"type": "string"},
                        "file_glob": {"type": "string", "description": "Example: *.py"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                    required=["query"],
                ),
                self.search_text,
            ),
            ToolSpec(
                "write_file",
                "Create or replace one UTF-8 text file. Parent directories are created automatically.",
                _object_schema(
                    {"path": {"type": "string"}, "content": {"type": "string"}},
                    required=["path", "content"],
                ),
                self.write_file,
                changes_state=True,
            ),
            ToolSpec(
                "edit_file",
                "Replace an exact text fragment in a UTF-8 file. Fails unless the match count equals expected_replacements.",
                _object_schema(
                    {
                        "path": {"type": "string"},
                        "old_text": {"type": "string", "minLength": 1},
                        "new_text": {"type": "string"},
                        "expected_replacements": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    required=["path", "old_text", "new_text"],
                ),
                self.edit_file,
                changes_state=True,
            ),
            ToolSpec(
                "make_directory",
                "Create a directory and any missing parents inside the workspace.",
                _object_schema({"path": {"type": "string"}}, required=["path"]),
                self.make_directory,
                changes_state=True,
            ),
            ToolSpec(
                "run_command",
                "Run a shell command in the workspace and return stdout, stderr, exit code, and timeout status.",
                _object_schema(
                    {
                        "command": {"type": "string", "minLength": 1},
                        "cwd": {"type": "string", "description": "Relative working directory; default is ."},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
                    },
                    required=["command"],
                ),
                self.run_command,
                changes_state=True,
            ),
        ]
        return {spec.name: spec for spec in specs}

    def list_files(
        self, path: str = ".", pattern: str = "*", max_results: int = 300
    ) -> dict[str, Any]:
        _bounded_int(max_results, 1, 1000, "max_results")
        base = self.workspace.resolve(path, must_exist=True)
        if not base.is_dir():
            raise ToolError(f"Not a directory: {path}")
        entries: list[dict[str, Any]] = []
        for current, directories, files in os.walk(base):
            directories[:] = sorted(
                name for name in directories if name not in {".git", ".fyk-agent", "__pycache__"}
            )
            for name, kind in [(item, "directory") for item in directories] + [
                (item, "file") for item in sorted(files)
            ]:
                item_path = Path(current) / name
                relative = self.workspace.relative(item_path)
                if fnmatch.fnmatch(relative, pattern) or pattern == "*":
                    entry: dict[str, Any] = {"path": relative, "type": kind}
                    if kind == "file":
                        entry["size"] = item_path.stat().st_size
                    entries.append(entry)
                if len(entries) >= max_results:
                    return {"ok": True, "entries": entries, "truncated": True}
        return {"ok": True, "entries": entries, "truncated": False}

    def read_file(self, path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
        _bounded_int(offset, 1, 10_000_000, "offset")
        _bounded_int(limit, 1, 2000, "limit")
        target = self.workspace.resolve(path, must_exist=True)
        if not target.is_file():
            raise ToolError(f"Not a file: {path}")
        data = target.read_bytes()
        if len(data) > MAX_READ_BYTES:
            raise ToolError(
                f"File is {len(data)} bytes; limit is {MAX_READ_BYTES}. Use search_text or a smaller file."
            )
        if b"\x00" in data:
            raise ToolError("Binary files cannot be read")
        text = data.decode("utf-8")
        lines = text.splitlines()
        selected = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{number:>6} | {line}" for number, line in enumerate(selected, offset))
        return {
            "ok": True,
            "path": self.workspace.relative(target),
            "content": numbered,
            "total_lines": len(lines),
            "truncated": offset - 1 + limit < len(lines),
        }

    def search_text(
        self,
        query: str,
        path: str = ".",
        file_glob: str = "*",
        max_results: int = 100,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query:
            raise ToolError("query must be a non-empty string")
        _bounded_int(max_results, 1, 500, "max_results")
        base = self.workspace.resolve(path, must_exist=True)
        candidates = [base] if base.is_file() else base.rglob("*")
        matches: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate.is_file() or not fnmatch.fnmatch(candidate.name, file_glob):
                continue
            relative = self.workspace.relative(candidate)
            if any(part in {".git", ".fyk-agent", "__pycache__"} for part in candidate.parts):
                continue
            try:
                if candidate.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                data = candidate.read_bytes()
                if b"\x00" in data:
                    continue
                for line_number, line in enumerate(data.decode("utf-8").splitlines(), 1):
                    if query in line:
                        matches.append(
                            {"path": relative, "line": line_number, "text": line[:500]}
                        )
                        if len(matches) >= max_results:
                            return {"ok": True, "matches": matches, "truncated": True}
            except (OSError, UnicodeDecodeError):
                continue
        return {"ok": True, "matches": matches, "truncated": False}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        target = self.workspace.resolve(path, for_write=True)
        if target.exists() and not target.is_file():
            raise ToolError(f"Cannot overwrite a directory: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot_id = self.journal.capture(target)
        _atomic_write_text(target, content)
        return {
            "ok": True,
            "path": self.workspace.relative(target),
            "bytes_written": len(content.encode("utf-8")),
            "snapshot_id": snapshot_id,
        }

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        expected_replacements: int = 1,
    ) -> dict[str, Any]:
        _bounded_int(expected_replacements, 1, 100, "expected_replacements")
        if not old_text:
            raise ToolError("old_text must not be empty")
        target = self.workspace.resolve(path, must_exist=True, for_write=True)
        if not target.is_file():
            raise ToolError(f"Not a file: {path}")
        text = target.read_text(encoding="utf-8")
        actual = text.count(old_text)
        if actual != expected_replacements:
            raise ToolError(
                f"Expected {expected_replacements} exact match(es), found {actual}; file was not changed"
            )
        snapshot_id = self.journal.capture(target)
        updated = text.replace(old_text, new_text, expected_replacements)
        _atomic_write_text(target, updated)
        return {
            "ok": True,
            "path": self.workspace.relative(target),
            "replacements": expected_replacements,
            "snapshot_id": snapshot_id,
        }

    def make_directory(self, path: str) -> dict[str, Any]:
        target = self.workspace.resolve(path, for_write=True)
        existed = target.is_dir()
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": self.workspace.relative(target), "already_existed": existed}

    def run_command(
        self, command: str, cwd: str = ".", timeout: int = 120
    ) -> dict[str, Any]:
        if not isinstance(command, str) or not command.strip():
            raise ToolError("command must be a non-empty string")
        _bounded_int(timeout, 1, 600, "timeout")
        working_directory = self.workspace.resolve(cwd, must_exist=True)
        if not working_directory.is_dir():
            raise ToolError(f"Command cwd is not a directory: {cwd}")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        }
        try:
            completed = subprocess.run(
                command,
                cwd=working_directory,
                env=environment,
                shell=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
            )
            stdout, stdout_truncated = _truncate(completed.stdout, MAX_COMMAND_OUTPUT)
            stderr, stderr_truncated = _truncate(completed.stderr, MAX_COMMAND_OUTPUT)
            return {
                "ok": completed.returncode == 0,
                "command": command,
                "cwd": self.workspace.relative(working_directory),
                "exit_code": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "output_truncated": stdout_truncated or stderr_truncated,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_timeout_output(exc.stdout)
            stderr = _decode_timeout_output(exc.stderr)
            return {
                "ok": False,
                "command": command,
                "cwd": self.workspace.relative(working_directory),
                "exit_code": None,
                "stdout": _truncate(stdout, MAX_COMMAND_OUTPUT)[0],
                "stderr": _truncate(stderr, MAX_COMMAND_OUTPUT)[0],
                "output_truncated": len(stdout) > MAX_COMMAND_OUTPUT or len(stderr) > MAX_COMMAND_OUTPUT,
                "timed_out": True,
                "error": f"Command exceeded {timeout} seconds",
            }


def _object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _bounded_int(value: Any, minimum: int, maximum: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ToolError(f"{name} must be an integer between {minimum} and {maximum}")


def _atomic_write_text(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _failure(message: str, *, error_type: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "error_type": error_type}


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    half = limit // 2
    return value[:half] + "\n... output truncated ...\n" + value[-half:], True


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _result_summary(result: dict[str, Any]) -> str:
    safe = {key: value for key, value in result.items() if key not in {"content", "stdout", "stderr"}}
    return json.dumps(safe, ensure_ascii=False, default=str)[:1000]

