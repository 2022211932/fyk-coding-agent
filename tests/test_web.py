from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fyk_agent.config import Settings
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
        headers = {"X-FYK-Token": token}
        if origin:
            headers["Origin"] = origin
        return urlopen(Request(self.base_url + path, headers=headers), timeout=2)

    def post(self, path: str, payload: dict, *, token: str = "test-token"):
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-FYK-Token": token},
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

    def test_sensitive_edit_bodies_are_hidden_from_events(self) -> None:
        arguments = _safe_arguments({"path": "main.py", "content": "secret\nbody"})
        self.assertEqual(arguments["content"], "<11 characters>")
        self.assertEqual(arguments["content_lines"], 2)

        event = _safe_web_event(
            "tool_result", {"result": {"ok": True, "stdout": "x" * 9_000}}
        )
        self.assertEqual(len(event["result"]["stdout"]), 8_000)

    def test_approval_can_be_resolved_by_id(self) -> None:
        pending = PendingApproval()
        self.state.approvals["approval"] = pending
        self.assertTrue(self.state.resolve_approval("approval", "allow"))
        self.assertTrue(pending.event.is_set())
        self.assertEqual(pending.decision, "allow")
        self.assertFalse(self.state.resolve_approval("missing", "allow"))


if __name__ == "__main__":
    unittest.main()
