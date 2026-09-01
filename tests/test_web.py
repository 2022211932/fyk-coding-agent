from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fyk_agent.config import Settings
from fyk_agent.client import AssistantReply
from fyk_agent.tools import ToolRegistry
from fyk_agent.web import (
    PendingApproval,
    WebAgentState,
    _handler_factory,
    _safe_arguments,
    _safe_web_event,
    load_last_workspace,
)
from fyk_agent.workspace import Workspace


class WebConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        self.config_path = root / "host-config" / "settings.json"
        (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
        settings = Settings(api_key="test-key", base_url="https://example.invalid", model="test-model")
        self.state = WebAgentState(
            settings,
            Workspace(root),
            "test-token",
            3000,
            False,
            config_path=self.config_path,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, path: str, *, token: str = "test-token", origin: str | None = None):
        headers = {"X-Yukai-Token": token}
        if origin:
            headers["Origin"] = origin
        return urlopen(Request(self.base_url + path, headers=headers), timeout=2)

    def post(self, path: str, payload: dict, *, token: str = "test-token"):
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Yukai-Token": token},
            method="POST",
        )
        return urlopen(request, timeout=2)

    def test_status_requires_random_token(self) -> None:
        with self.assertRaises(HTTPError) as error:
            self.request("/api/status", token="wrong")
        self.assertEqual(error.exception.code, 403)

        with self.request("/api/status") as response:
            payload = json.load(response)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["engineering"]["phase"], "requirements")

    def test_engineering_mode_is_persisted_per_session(self) -> None:
        with self.post(
            "/api/sessions/update",
            {"session_id": "se-session", "engineering_mode": True},
        ) as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertTrue(self.state.session("se-session").engineering_mode)

        restored = WebAgentState(
            self.state.settings,
            Workspace(self.root),
            "new-token",
            3000,
            False,
            config_path=self.config_path,
        )
        self.assertTrue(restored.session("se-session").engineering_mode)

        with self.request("/api/engineering") as response:
            payload = json.load(response)
        self.assertEqual(payload["engineering"]["active_skill"]["id"], "requirements-analysis")

    def test_chat_can_run_with_engineering_mode_enabled(self) -> None:
        class FakeClient:
            def complete(self, _messages, _tools):
                return AssistantReply(
                    "工程模式已启动。",
                    [],
                    {"role": "assistant", "content": "工程模式已启动。"},
                )

        with patch("fyk_agent.web.OpenAICompatibleClient", return_value=FakeClient()):
            with self.post(
                "/api/chat",
                {
                    "session_id": "engineering-chat",
                    "message": "分析需求",
                    "engineering_mode": True,
                },
            ) as response:
                records = [json.loads(line) for line in response.read().decode("utf-8").splitlines()]

        self.assertTrue(any(item["type"] == "engineering_state" for item in records))
        self.assertEqual(records[-1]["stop_reason"], "completed")
        self.assertTrue(self.state.session("engineering-chat").engineering_mode)

    def test_cross_origin_browser_request_is_rejected(self) -> None:
        with self.assertRaises(HTTPError) as error:
            self.request("/api/status", origin="https://example.com")
        self.assertEqual(error.exception.code, 403)

    def test_files_endpoint_uses_workspace_tools(self) -> None:
        with self.request("/api/files?path=.") as response:
            payload = json.load(response)
        self.assertTrue(payload["ok"])
        self.assertIn("main.py", [entry["path"] for entry in payload["entries"]])

    def test_automatic_approval_can_be_toggled(self) -> None:
        with self.post("/api/settings", {"automatic_approval": True}) as response:
            payload = json.load(response)
        self.assertTrue(payload["automatic_approval"])
        self.assertTrue(self.state.automatic_approval)

        with self.assertRaises(HTTPError) as error:
            self.post("/api/settings", {"automatic_approval": "yes"})
        self.assertEqual(error.exception.code, 400)

    def test_browse_and_switch_local_workspace(self) -> None:
        other = self.root / "another-project"
        child = other / "src"
        child.mkdir(parents=True)

        query = urlencode({"path": str(other)})
        with self.request(f"/api/directories?{query}") as response:
            listing = json.load(response)
        self.assertEqual(listing["current"], str(other.resolve()))
        self.assertIn("src", [entry["name"] for entry in listing["entries"]])

        with self.post("/api/workspace", {"path": str(other)}) as response:
            payload = json.load(response)
        self.assertEqual(payload["workspace"], str(other.resolve()))
        self.assertEqual(load_last_workspace(self.config_path), other.resolve())

        with self.request("/api/projects") as response:
            projects = json.load(response)
        self.assertEqual(projects["current"], str(other.resolve()))
        self.assertEqual(projects["recent"][0], str(other.resolve()))

    def test_directory_browser_rejects_relative_paths(self) -> None:
        with self.assertRaises(HTTPError) as error:
            self.request("/api/directories?path=relative")
        self.assertEqual(error.exception.code, 400)

    def test_workspace_cannot_change_while_a_task_is_running(self) -> None:
        other = self.root / "another-project"
        other.mkdir()
        self.state.session("busy").running = True

        with self.assertRaises(HTTPError) as error:
            self.post("/api/workspace", {"path": str(other)})
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.state.workspace.root, self.root.resolve())

    def test_session_can_be_deleted_without_affecting_other_sessions(self) -> None:
        self.state.session("first").history = [{"role": "user", "content": "first"}]
        second = self.state.session("second")
        second.history = [{"role": "user", "content": "second"}]

        with self.post("/api/sessions/delete", {"session_id": "first"}) as response:
            payload = json.load(response)

        self.assertTrue(payload["ok"])
        self.assertNotIn("first", self.state.sessions)
        self.assertIs(self.state.session("second"), second)
        self.assertEqual(second.history, [{"role": "user", "content": "second"}])

    def test_sessions_survive_state_restart(self) -> None:
        self.state.update_session("saved", title="持久化测试", pinned=True)
        self.state.append_session_event("saved", {"type": "user", "message": "hello"})
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ]
        self.state.save_session_history("saved", history, context_compactions=2)

        restored = WebAgentState(
            self.state.settings,
            Workspace(self.root),
            "new-token",
            3000,
            False,
            config_path=self.config_path,
        )
        session = restored.session("saved")
        self.assertEqual(session.title, "持久化测试")
        self.assertTrue(session.pinned)
        self.assertEqual(session.history, history)
        self.assertEqual(session.events[0]["message"], "hello")
        self.assertEqual(session.context_compactions, 2)

    def test_sessions_endpoint_returns_real_context_size(self) -> None:
        history = [{"role": "system", "content": "system"}]
        self.state.save_session_history("stats", history, context_compactions=1)
        with self.request("/api/sessions") as response:
            payload = json.load(response)
        session = next(item for item in payload["sessions"] if item["id"] == "stats")
        self.assertEqual(session["message_count"], 1)
        self.assertGreater(session["context_chars"], 0)
        self.assertEqual(session["context_compactions"], 1)

    def test_session_storage_strips_reasoning_and_coalesces_state_events(self) -> None:
        self.state.append_session_event("compact", {"type": "context_stats", "context_chars": 10})
        self.state.append_session_event("compact", {"type": "context_stats", "context_chars": 20})
        self.state.append_session_event("compact", {"type": "engineering_state", "engineering": {"phase": "design"}})
        self.state.append_session_event("compact", {"type": "engineering_state", "engineering": {"phase": "implementation"}})
        self.state.save_session_history(
            "compact",
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "done", "reasoning_content": "hidden" * 100},
            ],
            context_compactions=1,
        )
        session = self.state.session("compact")
        self.assertEqual([item["type"] for item in session.events].count("context_stats"), 1)
        self.assertEqual([item["type"] for item in session.events].count("engineering_state"), 1)
        self.assertNotIn("reasoning_content", session.history[-1])
        self.assertEqual(session.events[-1]["engineering"]["phase"], "implementation")

    def test_running_session_can_be_stopped(self) -> None:
        session = self.state.session("busy")
        session.running = True
        with self.post("/api/sessions/cancel", {"session_id": "busy"}) as response:
            payload = json.load(response)
        self.assertTrue(payload["ok"])
        self.assertTrue(session.cancel_event.is_set())

    def test_diff_endpoint_uses_snapshot_and_path(self) -> None:
        registry = ToolRegistry(Workspace(self.root), approve=lambda _name, _args: True)
        changed = registry.execute(
            "edit_file",
            {"path": "main.py", "old_text": "hello", "new_text": "changed"},
        )
        query = urlencode({"snapshot_id": changed["snapshot_id"], "path": "main.py"})
        with self.request(f"/api/diff?{query}") as response:
            payload = json.load(response)
        self.assertIn("-print('hello')", payload["diff"])
        self.assertIn("+print('changed')", payload["diff"])

    def test_running_session_cannot_be_deleted(self) -> None:
        self.state.session("busy").running = True

        with self.assertRaises(HTTPError) as error:
            self.post("/api/sessions/delete", {"session_id": "busy"})

        self.assertEqual(error.exception.code, 409)
        self.assertIn("busy", self.state.sessions)

    def test_legacy_recent_project_is_migrated_on_next_start(self) -> None:
        legacy = self.root / "fyk-coding-agent" / "settings.json"
        legacy.parent.mkdir()
        legacy.write_text(
            json.dumps({"recent_workspaces": [str(self.root)]}),
            encoding="utf-8",
        )
        with patch("fyk_agent.web._settings_base_directory", return_value=self.root):
            self.assertEqual(load_last_workspace(), self.root.resolve())

    def test_sensitive_edit_bodies_are_hidden_from_events(self) -> None:
        arguments = _safe_arguments({"path": "main.py", "content": "secret\nbody"})
        self.assertEqual(arguments["content"], "<11 characters>")
        self.assertEqual(arguments["content_lines"], 2)

        event = _safe_web_event(
            "tool_result", {"result": {"ok": True, "stdout": "x" * 9_000}}
        )
        self.assertEqual(len(event["result"]["stdout"]), 8_000)

    def test_approval_can_be_resolved_by_id(self) -> None:
        pending = PendingApproval(session_id="approval-session")
        self.state.approvals["approval"] = pending
        self.assertTrue(self.state.resolve_approval("approval", "allow"))
        self.assertTrue(pending.event.is_set())
        self.assertEqual(pending.decision, "allow")
        decision = self.state.sessions["approval-session"].events[-1]
        self.assertEqual(decision["approval_id"], "approval")
        self.assertEqual(decision["decision"], "allow")
        self.assertFalse(self.state.resolve_approval("missing", "allow"))

    def test_automatic_mode_still_requests_high_risk_confirmation(self) -> None:
        self.state.automatic_approval = True
        emitted: list[tuple[str, dict]] = []
        approval_ready = threading.Event()
        result: list[bool] = []

        def emit(kind: str, payload: dict) -> None:
            emitted.append((kind, payload))
            approval_ready.set()

        worker = threading.Thread(
            target=lambda: result.append(
                self.state.request_approval(
                    "run_command",
                    {"command": "git reset --hard"},
                    emit,
                    force_manual=True,
                    risk_reason="command can discard Git data",
                )
            ),
            daemon=True,
        )
        worker.start()
        self.assertTrue(approval_ready.wait(timeout=2))
        event = emitted[0][1]
        self.assertTrue(event["force_manual"])
        self.assertIn("Git", event["risk_reason"])
        self.assertTrue(self.state.resolve_approval(event["approval_id"], "allow"))
        worker.join(timeout=2)

        self.assertEqual(result, [True])
        self.assertTrue(self.state.automatic_approval)

    def test_high_risk_allow_all_does_not_enable_automatic_mode(self) -> None:
        emitted: list[dict] = []
        approval_ready = threading.Event()
        result: list[bool] = []

        def emit(_kind: str, payload: dict) -> None:
            emitted.append(payload)
            approval_ready.set()

        worker = threading.Thread(
            target=lambda: result.append(
                self.state.request_approval(
                    "run_command",
                    {"command": "git reset --hard"},
                    emit,
                    force_manual=True,
                    risk_reason="command can discard Git data",
                )
            ),
            daemon=True,
        )
        worker.start()
        self.assertTrue(approval_ready.wait(timeout=2))
        self.assertTrue(self.state.resolve_approval(emitted[0]["approval_id"], "allow_all"))
        worker.join(timeout=2)

        self.assertEqual(result, [True])
        self.assertFalse(self.state.automatic_approval)


if __name__ == "__main__":
    unittest.main()
