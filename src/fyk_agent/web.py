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
from .context import message_size
from .engineering import EngineeringError, EngineeringWorkflow
from .tools import ToolRegistry
from .workspace import Workspace


@dataclass
class PendingApproval:
    event: threading.Event = field(default_factory=threading.Event)
    decision: str = "reject"
    session_id: str = ""


@dataclass
class WebSession:
    history: list[dict[str, Any]] | None = None
    title: str = "新会话"
    events: list[dict[str, Any]] = field(default_factory=list)
    pinned: bool = False
    archived: bool = False
    updated_at: float = field(default_factory=lambda: time.time() * 1000)
    context_compactions: int = 0
    engineering_mode: bool = False
    running: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
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
        self.sessions: dict[str, WebSession] = _load_web_sessions(workspace)
        self.engineering = EngineeringWorkflow(workspace.root)
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

    def delete_session(self, session_id: str) -> bool:
        with self.state_lock:
            with self.sessions_lock:
                session = self.sessions.get(session_id)
                if session is None:
                    return True
                with session.lock:
                    if session.running:
                        return False
                del self.sessions[session_id]
                self._persist_sessions_locked()
                return True

    def sessions_payload(self) -> dict[str, Any]:
        with self.state_lock:
            with self.sessions_lock:
                sessions = [
                    {
                        "id": session_id,
                        "title": session.title,
                        "events": session.events,
                        "pinned": session.pinned,
                        "archived": session.archived,
                        "updated_at": session.updated_at,
                        "context_chars": sum(message_size(message) for message in session.history or []),
                        "message_count": len(session.history or []),
                        "context_compactions": session.context_compactions,
                        "engineering_mode": session.engineering_mode,
                    }
                    for session_id, session in self.sessions.items()
                ]
                sessions.sort(key=lambda item: float(item["updated_at"]), reverse=True)
                return {"ok": True, "sessions": sessions}

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        engineering_mode: bool | None = None,
    ) -> WebSession:
        with self.state_lock:
            with self.sessions_lock:
                session = self.sessions.setdefault(session_id, WebSession())
                if title is not None:
                    session.title = title.strip()[:100] or "新会话"
                if pinned is not None:
                    session.pinned = pinned
                if archived is not None:
                    session.archived = archived
                if engineering_mode is not None:
                    session.engineering_mode = engineering_mode
                session.updated_at = time.time() * 1000
                self._persist_sessions_locked()
                return session

    def append_session_event(self, session_id: str, event: dict[str, Any]) -> None:
        with self.state_lock:
            with self.sessions_lock:
                session = self.sessions.setdefault(session_id, WebSession())
                record = dict(event)
                record.setdefault("timestamp", time.strftime("%H:%M:%S"))
                session.events.append(record)
                session.events = session.events[-2000:]
                session.updated_at = time.time() * 1000
                self._persist_sessions_locked()

    def save_session_history(
        self,
        session_id: str,
        history: list[dict[str, Any]],
        *,
        context_compactions: int,
    ) -> None:
        with self.state_lock:
            with self.sessions_lock:
                session = self.sessions.setdefault(session_id, WebSession())
                session.history = history
                session.context_compactions = context_compactions
                session.updated_at = time.time() * 1000
                self._persist_sessions_locked()

    def clear_session(self, session_id: str) -> bool:
        with self.state_lock:
            with self.sessions_lock:
                session = self.sessions.setdefault(session_id, WebSession())
                with session.lock:
                    if session.running:
                        return False
                    session.history = None
                    session.events = []
                    session.context_compactions = 0
                    session.updated_at = time.time() * 1000
                self._persist_sessions_locked()
                return True

    def cancel_session(self, session_id: str) -> bool:
        with self.state_lock:
            with self.sessions_lock:
                session = self.sessions.get(session_id)
                if session is None:
                    return False
                with session.lock:
                    if not session.running:
                        return False
                    session.cancel_event.set()
        with self.approvals_lock:
            for pending in self.approvals.values():
                if pending.session_id == session_id:
                    pending.decision = "reject"
                    pending.event.set()
        return True

    def _persist_sessions_locked(self) -> None:
        path = self.workspace.root / ".yukai" / "web_sessions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "sessions": [
                {
                    "id": session_id,
                    "title": session.title,
                    "history": session.history,
                    "events": session.events,
                    "pinned": session.pinned,
                    "archived": session.archived,
                    "updated_at": session.updated_at,
                    "context_compactions": session.context_compactions,
                    "engineering_mode": session.engineering_mode,
                }
                for session_id, session in sorted(
                    self.sessions.items(), key=lambda item: item[1].updated_at, reverse=True
                )[:50]
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=True, default=str)
            handle.write("\n")
            temporary = Path(handle.name)
        try:
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def status(self) -> dict[str, Any]:
        with self.state_lock:
            return {
                "ok": True,
                "version": __version__,
                "model": self.settings.model,
                "workspace": str(self.workspace.root),
                "automatic_approval": self.automatic_approval,
                "max_context_chars": self.settings.max_context_chars,
                "engineering": self.engineering.payload(),
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
                self.sessions = _load_web_sessions(selected)
                self.engineering = EngineeringWorkflow(selected.root)
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
        *,
        session_id: str = "default",
        force_manual: bool = False,
        risk_reason: str = "",
    ) -> bool:
        if self.automatic_approval and not force_manual:
            return True
        approval_id = secrets.token_urlsafe(12)
        pending = PendingApproval(session_id=session_id)
        with self.approvals_lock:
            self.approvals[approval_id] = pending
        emit(
            "approval_required",
            {
                "approval_id": approval_id,
                "tool": name,
                "arguments": _safe_arguments(arguments),
                "force_manual": force_manual,
                "risk_reason": risk_reason,
            },
        )
        pending.event.wait(timeout=300)
        with self.approvals_lock:
            self.approvals.pop(approval_id, None)
        if pending.decision == "allow_all":
            if not force_manual:
                self.set_automatic_approval(True)
            return True
        return pending.decision == "allow"

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        session_id = ""
        with self.approvals_lock:
            pending = self.approvals.get(approval_id)
            if pending is None:
                return False
            pending.decision = decision
            session_id = pending.session_id
            pending.event.set()
        if session_id:
            labels = {"allow": "已允许一次", "allow_all": "已开启自动审批", "reject": "已拒绝"}
            self.append_session_event(
                session_id,
                {
                    "type": "approval_decision",
                    "message": labels[decision],
                    "approval_id": approval_id,
                    "decision": decision,
                },
            )
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
        print(f"Yukai Console: http://localhost:{frontend_port}/")
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
        server_version = "YukaiWeb/0.3.2"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._origin_allowed():
                self.send_error(403)
                return
            self.send_response(204)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Yukai-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                self._json(200, state.status())
                return
            if parsed.path == "/api/sessions":
                self._json(200, state.sessions_payload())
                return
            if parsed.path == "/api/engineering":
                self._json(200, {"ok": True, "engineering": state.engineering.payload()})
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
            if parsed.path == "/api/diff":
                query = parse_qs(parsed.query)
                snapshot_id = query.get("snapshot_id", [""])[0]
                path = query.get("path", [""])[0]
                if not snapshot_id or not path:
                    self._json(400, {"ok": False, "error": "snapshot_id and path are required"})
                    return
                try:
                    with state.state_lock:
                        workspace = state.workspace
                    result = ToolRegistry(workspace).journal.unified_diff(snapshot_id, path)
                    self._json(200, result)
                except (ValueError, OSError) as exc:
                    self._json(404, {"ok": False, "error": str(exc)})
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
            if parsed.path == "/api/sessions/update":
                session_id = str(body.get("session_id", ""))[:100]
                if not session_id:
                    self._json(400, {"ok": False, "error": "session_id is required"})
                    return
                title = body.get("title")
                pinned = body.get("pinned")
                archived = body.get("archived")
                engineering_mode = body.get("engineering_mode")
                if title is not None and not isinstance(title, str):
                    self._json(400, {"ok": False, "error": "title must be a string"})
                    return
                if pinned is not None and not isinstance(pinned, bool):
                    self._json(400, {"ok": False, "error": "pinned must be boolean"})
                    return
                if archived is not None and not isinstance(archived, bool):
                    self._json(400, {"ok": False, "error": "archived must be boolean"})
                    return
                if engineering_mode is not None and not isinstance(engineering_mode, bool):
                    self._json(400, {"ok": False, "error": "engineering_mode must be boolean"})
                    return
                state.update_session(
                    session_id,
                    title=title,
                    pinned=pinned,
                    archived=archived,
                    engineering_mode=engineering_mode,
                )
                self._json(200, {"ok": True})
                return
            if parsed.path == "/api/engineering/reset":
                with state.state_lock:
                    payload = state.engineering.reset()
                self._json(200, {"ok": True, "engineering": payload})
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
                result = ToolRegistry(workspace).undo_last()
                session_id = str(body.get("session_id", "default"))[:100]
                state.append_session_event(
                    session_id,
                    {
                        "type": "notice" if result.get("ok") else "error",
                        "message": f"已恢复 {result['path']}" if result.get("ok") else None,
                        "error": None if result.get("ok") else result.get("error"),
                    },
                )
                self._json(200, result)
                return
            if parsed.path == "/api/clear":
                session_id = str(body.get("session_id", "default"))[:100]
                cleared = state.clear_session(session_id)
                self._json(
                    200 if cleared else 409,
                    {"ok": cleared, "error": None if cleared else "A task is still running"},
                )
                return
            if parsed.path == "/api/sessions/delete":
                session_id = str(body.get("session_id", "default"))[:100]
                deleted = state.delete_session(session_id)
                self._json(200 if deleted else 409, {"ok": deleted, "error": None if deleted else "A task is still running"})
                return
            if parsed.path == "/api/sessions/cancel":
                session_id = str(body.get("session_id", "default"))[:100]
                cancelled = state.cancel_session(session_id)
                self._json(200 if cancelled else 409, {"ok": cancelled})
                return
            self._json(404, {"ok": False, "error": "Not found"})

        def _chat(self, body: dict[str, Any]) -> None:
            message = str(body.get("message", "")).strip()
            session_id = str(body.get("session_id", "default"))[:100]
            engineering_mode = body.get("engineering_mode", False)
            engineering_answer = body.get("engineering_answer")
            if not message or len(message) > 100_000:
                self._json(400, {"ok": False, "error": "message is empty or too large"})
                return
            if not isinstance(engineering_mode, bool):
                self._json(400, {"ok": False, "error": "engineering_mode must be boolean"})
                return
            if engineering_answer is not None and not isinstance(engineering_answer, dict):
                self._json(400, {"ok": False, "error": "engineering_answer must be an object"})
                return
            with state.state_lock:
                session = state.session(session_id)
                with session.lock:
                    if session.running:
                        self._json(409, {"ok": False, "error": "This session already has a running task"})
                        return
                    session.running = True
                    session.cancel_event.clear()
                workspace = state.workspace

            state.update_session(session_id, engineering_mode=engineering_mode)
            if engineering_answer is not None:
                try:
                    state.engineering.answer_question(
                        str(engineering_answer.get("question_id", "")),
                        option_id=str(engineering_answer.get("option_id", "")),
                        answer=str(engineering_answer.get("answer", "")),
                    )
                except EngineeringError as exc:
                    with session.lock:
                        session.running = False
                    self._json(409, {"ok": False, "error": str(exc)})
                    return

            if session.title == "新会话":
                state.update_session(session_id, title=_compact_title(message))
            state.append_session_event(
                session_id,
                {
                    "type": "engineering_decision" if engineering_answer is not None else "user",
                    "message": message,
                    "timestamp": time.strftime("%H:%M:%S"),
                },
            )

            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(kind: str, data: dict[str, Any]) -> None:
                payload = _safe_web_event(kind, data)
                state.append_session_event(
                    session_id,
                    {"type": kind, **payload, "timestamp": time.strftime("%H:%M:%S")},
                )
                self.wfile.write(
                    (json.dumps({"type": kind, **payload}, ensure_ascii=False, default=str) + "\n").encode(
                        "utf-8", errors="replace"
                    )
                )
                self.wfile.flush()

            registry = ToolRegistry(
                workspace,
                approve=lambda name, arguments: state.request_approval(
                    name, arguments, emit, session_id=session_id
                ),
                approve_risky=lambda name, arguments, reason: state.request_approval(
                    name,
                    arguments,
                    emit,
                    session_id=session_id,
                    force_manual=True,
                    risk_reason=reason,
                ),
                cancelled=session.cancel_event.is_set,
            )
            agent = CodingAgent(
                OpenAICompatibleClient(state.settings),
                registry,
                max_steps=state.settings.max_steps,
                max_context_chars=state.settings.max_context_chars,
                notify=emit,
                cancelled=session.cancel_event.is_set,
                engineering=state.engineering if engineering_mode else None,
            )
            agent.context.compactions = session.context_compactions
            try:
                emit("run_started", {"message": message})
                result = agent.run(message, history=session.history)
                state.save_session_history(
                    session_id,
                    result.messages,
                    context_compactions=result.context_compactions,
                )
                emit(
                    "final",
                    {
                        "text": result.final_text,
                        "steps": result.steps,
                        "stop_reason": result.stop_reason,
                        "compactions": result.context_compactions,
                        "context_compactions": session.context_compactions,
                        "context_chars": sum(message_size(message) for message in result.messages),
                        "message_count": len(result.messages),
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
            if not self._origin_allowed() or self.headers.get("X-Yukai-Token") != state.token:
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


def _load_web_sessions(workspace: Workspace) -> dict[str, WebSession]:
    path = workspace.root / ".yukai" / "web_sessions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    raw_sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
    sessions: dict[str, WebSession] = {}
    for raw in raw_sessions[:50] if isinstance(raw_sessions, list) else []:
        if not isinstance(raw, dict):
            continue
        session_id = str(raw.get("id", ""))[:100]
        if not session_id:
            continue
        raw_history = raw.get("history")
        history = (
            [dict(message) for message in raw_history if isinstance(message, dict)]
            if isinstance(raw_history, list)
            else None
        )
        raw_events = raw.get("events", [])
        events = (
            [dict(event) for event in raw_events[-2000:] if isinstance(event, dict)]
            if isinstance(raw_events, list)
            else []
        )
        try:
            updated_at = float(raw.get("updated_at", time.time() * 1000))
            context_compactions = max(0, int(raw.get("context_compactions", 0)))
        except (TypeError, ValueError):
            continue
        sessions[session_id] = WebSession(
            history=history,
            title=str(raw.get("title", "新会话"))[:100] or "新会话",
            events=events,
            pinned=bool(raw.get("pinned", False)),
            archived=bool(raw.get("archived", False)),
            updated_at=updated_at,
            context_compactions=context_compactions,
            engineering_mode=bool(raw.get("engineering_mode", False)),
        )
    return sessions


def _compact_title(message: str, limit: int = 34) -> str:
    normalized = " ".join(message.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _default_config_path() -> Path:
    return _settings_base_directory() / "yukai" / "settings.json"


def _settings_base_directory() -> Path:
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"])
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))


def load_recent_workspaces(config_path: Path | None = None) -> list[Path]:
    targets = [config_path] if config_path else [
        _default_config_path(),
        _settings_base_directory() / "fyk-coding-agent" / "settings.json",
    ]
    payload: Any = None
    for target in targets:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            break
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    if payload is None:
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
