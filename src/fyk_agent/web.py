from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen
import webbrowser

from . import __version__
from .agent import CodingAgent
from .client import ModelError, OpenAICompatibleClient
from .config import Settings
from .tools import ToolRegistry
from .workspace import Workspace


@dataclass
class PendingApproval:
    event: threading.Event = field(default_factory=threading.Event)
    decision: str = "reject"


@dataclass
class WebSession:
    history: list[dict[str, Any]] | None = None
    running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class WebAgentState:
    def __init__(
        self,
        settings: Settings,
        workspace: Workspace,
        token: str,
        frontend_port: int,
        automatic_approval: bool,
        config_path: Path | None = None,
    ):
        self.settings = settings
        self.workspace = workspace
        self.token = token
        self.frontend_port = frontend_port
        self.automatic_approval = automatic_approval
        self.config_path = config_path or _default_config_path()
        self.state_lock = threading.RLock()
        self.sessions: dict[str, WebSession] = {}
        self.sessions_lock = threading.Lock()
        self.approvals: dict[str, PendingApproval] = {}
        self.approvals_lock = threading.Lock()
        try:
            _remember_workspace(workspace.root, self.config_path)
        except OSError:
            # The console remains usable when the host configuration directory
            # is read-only; only recent-project persistence is unavailable.
            pass

    def session(self, session_id: str) -> WebSession:
        with self.sessions_lock:
            return self.sessions.setdefault(session_id, WebSession())

    def status(self) -> dict[str, Any]:
        with self.state_lock:
            return {
                "ok": True,
                "version": __version__,
                "model": self.settings.model,
                "workspace": str(self.workspace.root),
                "automatic_approval": self.automatic_approval,
            }

    def set_automatic_approval(self, enabled: bool) -> dict[str, Any]:
        with self.state_lock:
            self.automatic_approval = enabled
            return self.status()

    def select_workspace(self, path: str) -> tuple[bool, dict[str, Any]]:
        try:
            selected = Workspace(Path(path))
        except (ValueError, OSError) as exc:
            return False, {"ok": False, "error": str(exc)}
        with self.state_lock:
            with self.sessions_lock:
                if any(session.running for session in self.sessions.values()):
                    return False, {"ok": False, "error": "任务运行时不能切换工作区"}
                self.workspace = selected
                self.sessions.clear()
            result = self.status()
            try:
                _remember_workspace(selected.root, self.config_path)
            except OSError:
                result["warning"] = "项目已切换，但无法保存到最近项目"
            return True, result

    def projects(self) -> dict[str, Any]:
        with self.state_lock:
            recent = [str(path) for path in load_recent_workspaces(self.config_path)]
            return {
                "ok": True,
                "current": str(self.workspace.root),
                "recent": recent,
                "roots": _directory_roots(),
            }

    def directories(self, path: str | None) -> dict[str, Any]:
        if path is None:
            return {"ok": True, "current": None, "parent": None, "entries": _directory_roots()}
        selected = Path(path).expanduser()
        if not selected.is_absolute():
            raise ValueError("目录浏览只接受本地主机绝对路径")
        resolved = selected.resolve()
        if not resolved.is_dir():
            raise ValueError(f"目录不存在: {resolved}")
        entries: list[dict[str, str]] = []
        try:
            children = sorted(
                (item for item in resolved.iterdir() if item.is_dir()),
                key=lambda item: item.name.lower(),
            )
        except (OSError, PermissionError) as exc:
            raise ValueError(f"无法读取目录: {resolved}") from exc
        for child in children[:500]:
            entries.append({"name": child.name, "path": str(child), "type": "directory"})
        parent = None if resolved.parent == resolved else str(resolved.parent)
        return {"ok": True, "current": str(resolved), "parent": parent, "entries": entries}

    def request_approval(
        self,
        name: str,
        arguments: dict[str, Any],
        emit: Callable[[str, dict[str, Any]], None],
    ) -> bool:
        if self.automatic_approval:
            return True
        approval_id = secrets.token_urlsafe(12)
        pending = PendingApproval()
        with self.approvals_lock:
            self.approvals[approval_id] = pending
        emit(
            "approval_required",
            {"approval_id": approval_id, "tool": name, "arguments": _safe_arguments(arguments)},
        )
        pending.event.wait(timeout=300)
        with self.approvals_lock:
            self.approvals.pop(approval_id, None)
        if pending.decision == "allow_all":
            self.set_automatic_approval(True)
            return True
        return pending.decision == "allow"

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        with self.approvals_lock:
            pending = self.approvals.get(approval_id)
            if pending is None:
                return False
            pending.decision = decision
            pending.event.set()
            return True


def run_web_console(
    settings: Settings,
    workspace: Workspace,
    *,
    automatic_approval: bool = False,
    api_port: int = 8765,
    frontend_port: int = 3000,
    open_browser: bool = True,
) -> int:
    web_root = Path(__file__).resolve().parents[2] / "web"
    if not (web_root / "package.json").is_file():
        raise RuntimeError(f"Web frontend not found at {web_root}")
    if not (web_root / "node_modules").is_dir():
        raise RuntimeError(
            f"Web dependencies are not installed. Run `npm install` in {web_root} first."
        )
    node = shutil.which("node.exe" if os.name == "nt" else "node")
    vinext_cli = web_root / "node_modules" / "vinext" / "dist" / "cli.js"
    if node is None or not vinext_cli.is_file():
        raise RuntimeError("Node.js 22+ and the installed web dependencies are required")

    token = secrets.token_urlsafe(32)
    state = WebAgentState(settings, workspace, token, frontend_port, automatic_approval)
    handler = _handler_factory(state)
    server = ThreadingHTTPServer(("127.0.0.1", api_port), handler)
    server.daemon_threads = True

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    frontend = subprocess.Popen(
        [
            node,
            str(vinext_cli),
            "dev",
            "--port",
            str(frontend_port),
        ],
        cwd=web_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    try:
        _wait_for_url(f"http://localhost:{frontend_port}/", frontend)
        query = urlencode(
            {"api": f"http://127.0.0.1:{api_port}", "token": token, "live": "1"}
        )
        frontend_url = f"http://localhost:{frontend_port}/?{query}"
        if open_browser:
            webbrowser.open(frontend_url)
        print(f"FYK Agent Console: http://localhost:{frontend_port}/")
        print(f"Workspace: {workspace.root}")
        print("Press Ctrl+C to stop the web console.")
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        # This function owns serve_forever in the current thread. Calling
        # shutdown() here can deadlock when frontend startup failed before
        # serve_forever began; closing the socket is sufficient after unwind.
        server.server_close()
        _stop_frontend(frontend)
    return 0


def _handler_factory(state: WebAgentState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "FYKAgentWeb/0.2"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._origin_allowed():
                self.send_error(403)
                return
            self.send_response(204)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-FYK-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                self._json(200, state.status())
                return
            if parsed.path == "/api/projects":
                self._json(200, state.projects())
                return
            if parsed.path == "/api/directories":
                query = parse_qs(parsed.query)
                path = query.get("path", [None])[0]
                try:
                    self._json(200, state.directories(path))
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                return
            if parsed.path == "/api/files":
                query = parse_qs(parsed.query)
                path = query.get("path", ["."])[0]
                with state.state_lock:
                    workspace = state.workspace
                registry = ToolRegistry(workspace)
                self._json(200, registry.execute("list_files", {"path": path, "max_results": 300}))
                return
            self._json(404, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            body = self._read_json()
            if body is None:
                return
            if parsed.path == "/api/chat":
                self._chat(body)
                return
            if parsed.path == "/api/settings":
                enabled = body.get("automatic_approval")
                if not isinstance(enabled, bool):
                    self._json(400, {"ok": False, "error": "automatic_approval must be boolean"})
                    return
                self._json(200, state.set_automatic_approval(enabled))
                return
            if parsed.path == "/api/workspace":
                path = body.get("path")
                if not isinstance(path, str) or not path.strip():
                    self._json(400, {"ok": False, "error": "path must be a non-empty string"})
                    return
                selected, result = state.select_workspace(path)
                self._json(200 if selected else 409, result)
                return
            approval_match = re.fullmatch(r"/api/approvals/([A-Za-z0-9_-]+)", parsed.path)
            if approval_match:
                decision = str(body.get("decision", "reject"))
                if decision not in {"allow", "allow_all", "reject"}:
                    self._json(400, {"ok": False, "error": "Invalid decision"})
                    return
                found = state.resolve_approval(approval_match.group(1), decision)
                self._json(200 if found else 404, {"ok": found})
                return
            if parsed.path == "/api/undo":
                with state.state_lock:
                    workspace = state.workspace
                self._json(200, ToolRegistry(workspace).undo_last())
                return
            if parsed.path == "/api/clear":
                session_id = str(body.get("session_id", "default"))[:100]
                with state.state_lock:
                    session = state.session(session_id)
                    with session.lock:
                        if session.running:
                            self._json(409, {"ok": False, "error": "A task is still running"})
                            return
                        session.history = None
                self._json(200, {"ok": True})
                return
            self._json(404, {"ok": False, "error": "Not found"})

        def _chat(self, body: dict[str, Any]) -> None:
            message = str(body.get("message", "")).strip()
            session_id = str(body.get("session_id", "default"))[:100]
            if not message or len(message) > 100_000:
                self._json(400, {"ok": False, "error": "message is empty or too large"})
                return
            with state.state_lock:
                session = state.session(session_id)
                with session.lock:
                    if session.running:
                        self._json(409, {"ok": False, "error": "This session already has a running task"})
                        return
                    session.running = True
                workspace = state.workspace

            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(kind: str, data: dict[str, Any]) -> None:
                payload = _safe_web_event(kind, data)
                self.wfile.write(
                    (json.dumps({"type": kind, **payload}, ensure_ascii=False, default=str) + "\n").encode(
                        "utf-8", errors="replace"
                    )
                )
                self.wfile.flush()

            registry = ToolRegistry(
                workspace,
                approve=lambda name, arguments: state.request_approval(name, arguments, emit),
            )
            agent = CodingAgent(
                OpenAICompatibleClient(state.settings),
                registry,
                max_steps=state.settings.max_steps,
                max_context_chars=state.settings.max_context_chars,
                notify=emit,
            )
            try:
                emit("run_started", {"message": message})
                result = agent.run(message, history=session.history)
                session.history = result.messages
                emit(
                    "final",
                    {
                        "text": result.final_text,
                        "steps": result.steps,
                        "stop_reason": result.stop_reason,
                        "compactions": result.context_compactions,
                    },
                )
            except (ModelError, ValueError, RuntimeError) as exc:
                emit("error", {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with state.state_lock:
                    with session.lock:
                        session.running = False
                self.close_connection = True

        def _authorized(self) -> bool:
            if not self._origin_allowed() or self.headers.get("X-FYK-Token") != state.token:
                self._json(403, {"ok": False, "error": "Forbidden"}, include_cors=False)
                return False
            return True

        def _origin_allowed(self) -> bool:
            origin = self.headers.get("Origin")
            return origin in {
                None,
                f"http://localhost:{state.frontend_port}",
                f"http://127.0.0.1:{state.frontend_port}",
            }

        def _cors_headers(self) -> None:
            origin = self.headers.get("Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _read_json(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_000_000:
                    raise ValueError("Invalid request size")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("JSON body must be an object")
                return value
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return None

        def _json(
            self,
            status: int,
            value: dict[str, Any],
            *,
            include_cors: bool = True,
        ) -> None:
            payload = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            if include_cors:
                self._cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in {"content", "old_text", "new_text"} and isinstance(value, str):
            safe[key] = f"<{len(value)} characters>"
            safe[f"{key}_lines"] = len(value.splitlines())
        else:
            safe[key] = value
    return safe


def _default_config_path() -> Path:
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "fyk-coding-agent" / "settings.json"


def load_recent_workspaces(config_path: Path | None = None) -> list[Path]:
    target = config_path or _default_config_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    raw_paths = payload.get("recent_workspaces", []) if isinstance(payload, dict) else []
    recent: list[Path] = []
    for raw_path in raw_paths if isinstance(raw_paths, list) else []:
        try:
            path = Path(str(raw_path)).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if path.is_dir() and path not in recent:
            recent.append(path)
    return recent[:10]


def load_last_workspace(config_path: Path | None = None) -> Path | None:
    recent = load_recent_workspaces(config_path)
    return recent[0] if recent else None


def _remember_workspace(path: Path, config_path: Path) -> None:
    resolved = path.resolve()
    recent = [resolved, *(item for item in load_recent_workspaces(config_path) if item != resolved)]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"recent_workspaces": [str(item) for item in recent[:10]]},
        ensure_ascii=False,
        indent=2,
    )
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=config_path.parent, delete=False
    ) as handle:
        handle.write(payload + "\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, config_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _directory_roots() -> list[dict[str, str]]:
    if os.name == "nt":
        roots = [Path(f"{chr(letter)}:\\") for letter in range(ord("A"), ord("Z") + 1)]
        return [
            {"name": str(root), "path": str(root), "type": "root"}
            for root in roots
            if root.exists()
        ]
    return [{"name": "/", "path": "/", "type": "root"}]


def _safe_web_event(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    safe = dict(data)
    if kind == "tool_call":
        arguments = data.get("arguments", {})
        safe["arguments"] = _safe_arguments(arguments) if isinstance(arguments, dict) else {}
    if kind == "tool_result":
        raw_result = data.get("result", {})
        result = dict(raw_result) if isinstance(raw_result, dict) else {"ok": False}
        for key in ("content", "stdout", "stderr"):
            if isinstance(result.get(key), str):
                result[key] = result[key][:8_000]
        safe["result"] = result
    return safe


def _wait_for_url(url: str, process: subprocess.Popen[Any], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Frontend process exited before it became ready")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Frontend did not start within {timeout:.0f} seconds")


def _stop_frontend(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
