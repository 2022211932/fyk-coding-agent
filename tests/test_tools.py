import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from fyk_agent.tools import ToolRegistry, assess_tool_risk
from fyk_agent.workspace import Workspace


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = ToolRegistry(
            Workspace(self.root),
            approve=lambda _name, _args: True,
            approve_risky=lambda _name, _args, _reason: True,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_write_read_edit_and_undo(self) -> None:
        written = self.registry.execute("write_file", {"path": "src/app.py", "content": "value = 1\n"})
        self.assertTrue(written["ok"])
        read = self.registry.execute("read_file", {"path": "src/app.py"})
        self.assertTrue(read["ok"])
        self.assertIn("value = 1", read["content"])

        edited = self.registry.execute(
            "edit_file",
            {"path": "src/app.py", "old_text": "value = 1", "new_text": "value = 2"},
        )
        self.assertTrue(edited["ok"])
        self.assertEqual((self.root / "src" / "app.py").read_text(encoding="utf-8"), "value = 2\n")

        undone = self.registry.undo_last()
        self.assertTrue(undone["ok"])
        self.assertEqual((self.root / "src" / "app.py").read_text(encoding="utf-8"), "value = 1\n")
        self.registry.undo_last()
        self.assertFalse((self.root / "src" / "app.py").exists())

    def test_file_change_exposes_unified_diff(self) -> None:
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        edited = self.registry.execute(
            "edit_file",
            {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"},
        )
        diff = self.registry.journal.unified_diff(edited["snapshot_id"], "app.py")
        self.assertIn("-value = 1", diff["diff"])
        self.assertIn("+value = 2", diff["diff"])
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.registry.journal.unified_diff(edited["snapshot_id"], "other.py")

    def test_edit_requires_exact_match_count(self) -> None:
        (self.root / "data.txt").write_text("x x", encoding="utf-8")
        result = self.registry.execute(
            "edit_file",
            {"path": "data.txt", "old_text": "x", "new_text": "y", "expected_replacements": 1},
        )
        self.assertFalse(result["ok"])
        self.assertEqual((self.root / "data.txt").read_text(encoding="utf-8"), "x x")

    def test_search_text_and_list_files(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("needle = 1\n", encoding="utf-8")
        (self.root / "src" / "b.txt").write_text("nothing\n", encoding="utf-8")
        search = self.registry.execute(
            "search_text", {"query": "needle", "path": "src", "file_glob": "*.py"}
        )
        self.assertEqual(search["matches"][0]["path"], "src/a.py")
        listing = self.registry.execute("list_files", {"path": "src"})
        self.assertEqual({item["path"] for item in listing["entries"]}, {"src/a.py", "src/b.txt"})

    def test_recursive_tools_do_not_expose_dotenv(self) -> None:
        (self.root / ".env").write_text("DEEPSEEK_API_KEY=secret-marker\n", encoding="utf-8")
        (self.root / "safe.txt").write_text("secret-marker is documentation\n", encoding="utf-8")
        listing = self.registry.execute("list_files", {"path": "."})
        paths = {item["path"] for item in listing["entries"]}
        self.assertNotIn(".env", paths)
        search = self.registry.execute("search_text", {"query": "secret-marker", "path": "."})
        self.assertEqual([match["path"] for match in search["matches"]], ["safe.txt"])

    def test_rejected_write_does_not_change_file(self) -> None:
        registry = ToolRegistry(Workspace(self.root), approve=lambda _name, _args: False)
        result = registry.execute("write_file", {"path": "no.txt", "content": "blocked"})
        self.assertFalse(result["ok"])
        self.assertFalse((self.root / "no.txt").exists())

    def test_run_command_returns_exit_data_and_hides_secrets(self) -> None:
        original = os.environ.get("FYK_TEST_SECRET")
        os.environ["FYK_TEST_SECRET"] = "must-not-leak"
        try:
            command = f'"{sys.executable}" -c "import os; print(os.getenv(\'FYK_TEST_SECRET\', \'hidden\'))"'
            result = self.registry.execute("run_command", {"command": command, "timeout": 10})
        finally:
            if original is None:
                os.environ.pop("FYK_TEST_SECRET", None)
            else:
                os.environ["FYK_TEST_SECRET"] = original
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stdout"].strip(), "hidden")
        self.assertEqual(result["exit_code"], 0)

    def test_running_command_can_be_cancelled(self) -> None:
        cancelled = threading.Event()
        registry = ToolRegistry(
            Workspace(self.root),
            approve=lambda _name, _args: True,
            approve_risky=lambda _name, _args, _reason: True,
            cancelled=cancelled.is_set,
        )
        results: list[dict] = []
        command = f'"{sys.executable}" -c "import time; time.sleep(10)"'
        worker = threading.Thread(
            target=lambda: results.append(
                registry.execute("run_command", {"command": command, "timeout": 20})
            )
        )
        worker.start()
        time.sleep(0.35)
        cancelled.set()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0]["cancelled"])
        self.assertEqual(results[0]["error_type"], "cancelled")

    def test_catastrophic_command_is_blocked_before_approval(self) -> None:
        approvals: list[str] = []
        registry = ToolRegistry(
            Workspace(self.root),
            approve=lambda _name, _args: True,
            approve_risky=lambda _name, _args, reason: approvals.append(reason) or True,
        )

        commands = [
            "rm -rf /",
            "rm --recursive --force /",
            r"Remove-Item C:\ -Recurse -Force",
            "shutdown /s /t 0",
            "format C:",
        ]
        for command in commands:
            with self.subTest(command=command):
                result = registry.execute("run_command", {"command": command})
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_type"], "blocked_by_safety_policy")
        self.assertEqual(approvals, [])

    def test_high_risk_command_requires_explicit_approval(self) -> None:
        approvals: list[str] = []
        registry = ToolRegistry(
            Workspace(self.root),
            approve=lambda _name, _args: True,
            approve_risky=lambda _name, _args, reason: approvals.append(reason) or False,
        )

        result = registry.execute("run_command", {"command": "git reset --hard"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "rejected")
        self.assertIn("Git", approvals[0])

    def test_known_test_command_can_use_automatic_approval(self) -> None:
        risky_approvals: list[str] = []
        registry = ToolRegistry(
            Workspace(self.root),
            approve=lambda _name, _args: True,
            approve_risky=lambda _name, _args, reason: risky_approvals.append(reason) or False,
        )

        result = registry.execute(
            "run_command",
            {"command": "python -m compileall .", "timeout": 10},
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(risky_approvals, [])

    def test_unknown_command_is_high_risk_by_default(self) -> None:
        assessment = assess_tool_risk("run_command", {"command": "python custom_script.py"})
        self.assertEqual(assessment.level, "high")
        self.assertIn("allowlist", assessment.reason)


if __name__ == "__main__":
    unittest.main()
