from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
import locale
import os
from pathlib import Path
import platform
import re
import signal
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
class RiskAssessment:
    level: str
    reason: str = ""


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
        approve_risky: Callable[[str, dict[str, Any], str], bool] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ):
        self.workspace = workspace
        self.journal = ChangeJournal(workspace.root)
        self.events = EventLog(workspace.root)
        self.approve = approve or (lambda _name, _arguments: True)
        self.approve_risky = approve_risky or (lambda _name, _arguments, _reason: False)
        self.cancelled = cancelled or (lambda: False)
        self._specs = self._build_specs()

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [spec.api_schema() for spec in self._specs.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.cancelled():
            return _failure("Task was stopped", error_type="cancelled", cancelled=True)
        spec = self._specs.get(name)
        if spec is None:
            result = _failure(f"Unknown tool: {name}", error_type="unknown_tool")
            self.events.emit("tool_error", tool=name, error=result["error"])
            return result
        if not isinstance(arguments, dict):
            return _failure("Tool arguments must be a JSON object", error_type="invalid_arguments")
        if name == "run_command":
            try:
                preflight_result = self._preflight_run_command(arguments)
            except (ToolError, WorkspaceError, OSError, UnicodeError) as exc:
                preflight_result = _failure(str(exc), error_type=type(exc).__name__)
            if preflight_result is not None:
                preflight_result["duration_ms"] = 0
                self.events.emit(
                    "tool_finished",
                    tool=name,
                    ok=False,
                    duration_ms=0,
                    summary=_result_summary(preflight_result),
                )
                return preflight_result
        risk = assess_tool_risk(name, arguments)
        if risk.level == "blocked":
            result = _failure(
                f"Blocked by safety policy: {risk.reason}",
                error_type="blocked_by_safety_policy",
            )
            self.events.emit("tool_blocked", tool=name, reason=risk.reason)
            return result
        if spec.changes_state:
            approved = (
                self.approve_risky(name, arguments, risk.reason)
                if risk.level == "high"
                else self.approve(name, arguments)
            )
            if not approved:
                message = "User rejected this high-risk operation" if risk.level == "high" else "User rejected this operation"
                result = _failure(message, error_type="rejected")
                self.events.emit("tool_rejected", tool=name, risk=risk.level)
                return result

        # Execution duration deliberately starts after any human approval wait.
        started = time.monotonic()
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
                "get_environment",
                "Read the current operating system, shell, Python runtime, and workspace path before choosing platform-specific commands.",
                _object_schema({}),
                self.get_environment,
            ),
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

    def get_environment(self) -> dict[str, Any]:
        shell = os.environ.get("COMSPEC" if os.name == "nt" else "SHELL")
        if not shell:
            shell = "cmd.exe" if os.name == "nt" else "/bin/sh"
        return {
            "ok": True,
            "os": platform.system() or os.name,
            "platform": platform.platform(),
            "shell": shell,
            "path_separator": os.sep,
            "python": platform.python_version(),
            "workspace": str(self.workspace.root),
        }

    def _preflight_run_command(self, arguments: dict[str, Any]) -> dict[str, Any] | None:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        if os.name == "nt":
            incompatible = [
                (r"^\s*ls(?:\s|$)", "Use list_files, or use dir for a non-recursive cmd listing"),
                (
                    r"^\s*find\s+(?:\.|/)[^\r\n]*\s-(?:maxdepth|mindepth|type|name)\b",
                    "Use list_files/search_text, or use a command valid for cmd.exe",
                ),
                (r"^\s*(?:cat|pwd|which|grep)(?:\s|$)", "Use the corresponding structured Yukai tool"),
            ]
            for pattern, suggestion in incompatible:
                if re.search(pattern, command, flags=re.IGNORECASE):
                    return _failure(
                        "Command syntax is incompatible with the configured Windows cmd.exe shell",
                        error_type="incompatible_shell_command",
                        shell=os.environ.get("COMSPEC", "cmd.exe"),
                        suggestion=suggestion,
                    )
        match = re.match(
            r"^\s*(?:(?:npm(?:\.cmd)?|pnpm(?:\.cmd)?|yarn(?:\.cmd)?)\s+"
            r"(?:test(?:\s|$)|run\s+([A-Za-z0-9:_-]+)(?:\s|$))|"
            r"bun(?:\.exe)?\s+run\s+([A-Za-z0-9:_-]+)(?:\s|$))",
            command,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        script = match.group(1) or match.group(2) or "test"
        cwd = arguments.get("cwd", ".")
        working_directory = self.workspace.resolve(cwd, must_exist=True)
        if not working_directory.is_dir():
            raise ToolError(f"Command cwd is not a directory: {cwd}")
        relative_cwd = self.workspace.relative(working_directory)
        manifest_path = "package.json" if relative_cwd == "." else f"{relative_cwd}/package.json"
        manifest = self.workspace.resolve(manifest_path)
        if not manifest.is_file():
            return _failure(
                f"Cannot run package script '{script}': package.json was not found in the command working directory",
                error_type="missing_project_manifest",
                cwd=relative_cwd,
                expected="package.json",
                script=script,
            )
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return _failure(
                f"Cannot read package.json: {exc}",
                error_type="invalid_project_manifest",
                cwd=relative_cwd,
                script=script,
            )
        scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
        if not isinstance(scripts, dict) or script not in scripts:
            available = sorted(str(name) for name in scripts) if isinstance(scripts, dict) else []
            return _failure(
                f"Cannot run package script '{script}': it is not defined in package.json",
                error_type="missing_package_script",
                cwd=relative_cwd,
                script=script,
                available_scripts=available,
            )
        return None

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
                name
                for name in directories
                if name not in {".git", ".yukai", ".fyk-agent", "__pycache__"}
            )
            for name, kind in [(item, "directory") for item in directories] + [
                (item, "file") for item in sorted(files)
            ]:
                item_path = Path(current) / name
                try:
                    relative = self.workspace.relative(item_path)
                    self.workspace.resolve(relative, must_exist=True)
                except (ValueError, WorkspaceError, OSError):
                    continue
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
            try:
                relative = self.workspace.relative(candidate)
                self.workspace.resolve(relative, must_exist=True)
            except (ValueError, WorkspaceError, OSError):
                continue
            if any(
                part in {".git", ".yukai", ".fyk-agent", "__pycache__"}
                for part in candidate.parts
            ):
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
        if os.name == "nt":
            command_processor = os.environ.get("COMSPEC", "cmd.exe")
            shell_command: list[str] | str = (
                f'"{command_processor}" /d /s /c "{command}"'
            )
        else:
            shell_command = ["/bin/sh", "-c", command]
        creation_options: dict[str, Any] = {}
        if os.name == "nt":
            creation_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creation_options["start_new_session"] = True
        process = subprocess.Popen(
                shell_command,
                cwd=working_directory,
                env=environment,
                shell=False,
                text=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **creation_options,
            )
        deadline = time.monotonic() + timeout
        while True:
            if self.cancelled():
                _terminate_process_tree(process)
                stdout, stderr = process.communicate()
                return _command_interrupted_result(
                    command,
                    self.workspace.relative(working_directory),
                    stdout,
                    stderr,
                    cancelled=True,
                    error="Task was stopped by the user",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(process)
                stdout, stderr = process.communicate()
                return _command_interrupted_result(
                    command,
                    self.workspace.relative(working_directory),
                    stdout,
                    stderr,
                    cancelled=False,
                    error=f"Command exceeded {timeout} seconds",
                )
            try:
                stdout_text, stderr_text = process.communicate(timeout=min(0.2, remaining))
            except subprocess.TimeoutExpired:
                continue
            stdout, stdout_truncated = _truncate(
                _sanitize_command_output(_decode_command_output(stdout_text)), MAX_COMMAND_OUTPUT
            )
            stderr, stderr_truncated = _truncate(
                _sanitize_command_output(_decode_command_output(stderr_text)), MAX_COMMAND_OUTPUT
            )
            return {
                "ok": process.returncode == 0,
                "command": command,
                "cwd": self.workspace.relative(working_directory),
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "output_truncated": stdout_truncated or stderr_truncated,
                "timed_out": False,
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


_SAFE_COMMAND_PATTERNS = [
    r"^(?:python|py)(?:\.exe)?\s+-m\s+(?:pytest|unittest|compileall)\b",
    r"^pytest\b",
    r"^(?:npm|pnpm|yarn|bun)\s+(?:test|run\s+(?:test|lint|build|check|typecheck))\b",
    r"^git\s+(?:status|diff|log|show)\b",
    r"^(?:rg|grep|ls|dir|pwd|tree|type|cat|where|which|get-childitem|get-content|select-string)\b",
    r"^(?:ruff|mypy|eslint|tsc)\b",
    r"^go\s+test\b",
    r"^cargo\s+(?:test|check)\b",
    r"^dotnet\s+(?:test|build)\b",
]

_BLOCKED_COMMAND_PATTERNS = [
    (r"(?:^|[/\\\s'\"])\.(?:yukai|fyk-agent)(?:[/\\\s'\"]|$)", "Yukai internal state directories cannot be accessed through shell commands"),
    (r"(?:^|\s)(?:shutdown|reboot|halt|poweroff|stop-computer|restart-computer)(?:\s|$)", "system shutdown and restart commands are not allowed"),
    (r"(?:^|\s)(?:mkfs(?:\.[a-z0-9]+)?|fdisk|parted|diskpart)(?:\s|$)", "disk formatting and partition commands are not allowed"),
    (r"\bdd\s+.*\bof\s*=\s*(?:/dev/|\\\\\.\\)", "raw writes to block devices are not allowed"),
    (r"(?:^|\s)format(?:\.com)?\s+[a-z]:", "formatting a drive is not allowed"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bombs are not allowed"),
    (r"\brm\b[^\r\n]*(?:-[a-z]*r|--recursive|--no-preserve-root)[^\r\n]*(?:\s/\*?(?:\s|$)|\s~(?:\s|$)|\s\$\{?home\}?(?:\s|$)|\s[a-z]:\\(?:\s|$))", "recursive deletion of a system or home root is not allowed"),
    (r"\bremove-item\b[^\r\n]*(?:^|\s)-recurse\b[^\r\n]*(?:[a-z]:\\(?=\s|$)|\$home\b|\$env:userprofile\b)", "recursive deletion of a drive or home directory is not allowed"),
    (r"\bremove-item\b[^\r\n]*(?:[a-z]:\\(?=\s|$)|\$home\b|\$env:userprofile\b)[^\r\n]*(?:^|\s)-recurse\b", "recursive deletion of a drive or home directory is not allowed"),
    (r"\b(?:del|rd|rmdir)\b[^\r\n]*(?:/s\b|/q\b)[^\r\n]*[a-z]:\\(?:\*\.?\*?)?(?:\s|$)", "recursive deletion of a drive root is not allowed"),
    (r"\b(?:del|rd|rmdir)\b[^\r\n]*[a-z]:\\(?:\*\.?\*?)?[^\r\n]*(?:/s\b|/q\b)", "recursive deletion of a drive root is not allowed"),
]

_HIGH_RISK_COMMAND_PATTERNS = [
    (r"(?:^|\s)(?:rm|del|erase|rmdir|rd|remove-item|unlink)(?:\s|$)", "command deletes files or directories"),
    (r"\bgit\s+(?:reset\s+--hard|clean\b|push\b[^\r\n]*(?:--force(?:-with-lease)?|-f\b)|restore\b|checkout\b[^\r\n]*\s--\s)", "command can discard or overwrite Git data"),
    (r"(?:^|\s)(?:sudo|su|runas|doas)(?:\s|$)|\bstart-process\b[^\r\n]*\b-verb\s+runas\b", "command requests elevated privileges"),
    (r"\b(?:chmod|chown|icacls|takeown|set-acl|reg\s+(?:add|delete))\b", "command changes permissions or system configuration"),
    (r"\b(?:curl|wget|invoke-webrequest|iwr)\b[^\r\n]*(?:\||invoke-expression|iex\b|\bsh\b|\bbash\b|powershell)", "command downloads and executes remote content"),
    (r"(?:\.\.[/\\]|(?:^|\s)[a-z]:\\|(?:^|\s)/(?:etc|usr|var|home|root|opt|bin|sbin|dev|proc|sys)(?:/|\s|$))", "command references a path outside the workspace"),
]


def assess_tool_risk(name: str, arguments: dict[str, Any]) -> RiskAssessment:
    if name != "run_command":
        return RiskAssessment("normal")
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return RiskAssessment("normal")
    normalized = " ".join(command.casefold().split())
    for pattern, reason in _BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return RiskAssessment("blocked", reason)
    for pattern, reason in _HIGH_RISK_COMMAND_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return RiskAssessment("high", reason)
    if re.search(r"(?:&&|\|\||[;|<>`]|\$\()", normalized):
        return RiskAssessment("high", "compound shell syntax can hide additional operations")
    if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _SAFE_COMMAND_PATTERNS):
        return RiskAssessment("normal")
    return RiskAssessment("high", "command is not on the automatic-execution allowlist")


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


def _failure(message: str, *, error_type: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "error_type": error_type, **details}


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _command_interrupted_result(
    command: str,
    cwd: str,
    stdout: bytes | str,
    stderr: bytes | str,
    *,
    cancelled: bool,
    error: str,
) -> dict[str, Any]:
    safe_stdout, stdout_truncated = _truncate(
        _sanitize_command_output(_decode_command_output(stdout)), MAX_COMMAND_OUTPUT
    )
    safe_stderr, stderr_truncated = _truncate(
        _sanitize_command_output(_decode_command_output(stderr)), MAX_COMMAND_OUTPUT
    )
    return {
        "ok": False,
        "command": command,
        "cwd": cwd,
        "exit_code": None,
        "stdout": safe_stdout,
        "stderr": safe_stderr,
        "output_truncated": stdout_truncated or stderr_truncated,
        "timed_out": not cancelled,
        "cancelled": cancelled,
        "error_type": "cancelled" if cancelled else "timeout",
        "error": error,
    }


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    half = limit // 2
    return value[:half] + "\n... output truncated ...\n" + value[-half:], True


def _decode_command_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    candidates = ["utf-8", locale.getpreferredencoding(False)]
    if os.name == "nt":
        candidates.extend(["mbcs", "cp936"])
    for encoding in dict.fromkeys(candidates):
        try:
            return value.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


def _sanitize_command_output(value: str) -> str:
    lines = value.splitlines(keepends=True)
    visible = [
        line
        for line in lines
        if not re.search(
            r"(?:^|[/\\\s'\"])\.(?:yukai|fyk-agent)(?:[/\\\s'\"]|$)",
            line,
            re.IGNORECASE,
        )
    ]
    removed = len(lines) - len(visible)
    if removed:
        visible.append(f"[Yukai internal state omitted: {removed} line(s)]\n")
    return "".join(visible)


def _result_summary(result: dict[str, Any]) -> str:
    safe = {key: value for key, value in result.items() if key not in {"content", "stdout", "stderr"}}
    return json.dumps(safe, ensure_ascii=False, default=str)[:1000]
