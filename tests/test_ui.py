from io import StringIO
from pathlib import Path
import unittest

from fyk_agent.cli import _safe_argument_summary
from fyk_agent.ui import TerminalUI


class TerminalUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = StringIO()
        self.errors = StringIO()
        self.ui = TerminalUI(color=False, stream=self.output, error_stream=self.errors)

    def test_banner_status_and_history_are_plain_text(self) -> None:
        self.ui.banner(
            version="0.3.2",
            model="deepseek-v4-pro",
            workspace=Path("workspace"),
            automatic_approval=False,
        )
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "inspect the project"},
            {"role": "assistant", "content": "done"},
        ]
        self.ui.status(
            model="deepseek-v4-pro",
            workspace=Path("workspace"),
            automatic_approval=False,
            history=history,
        )
        self.ui.history(history)
        rendered = self.output.getvalue()
        self.assertIn("Yukai 0.3.2", rendered)
        self.assertIn("1 user turn(s), 3 message(s)", rendered)
        self.assertIn("inspect the project", rendered)
        self.assertNotIn("\033[", rendered)

    def test_tool_progress_contains_safe_useful_details(self) -> None:
        self.ui.tool_call("edit_file", {"path": "src/app.py", "new_text": "secret body"})
        self.ui.tool_result(
            "edit_file", {"ok": True, "path": "src/app.py", "duration_ms": 12.4}
        )
        rendered = self.output.getvalue()
        self.assertIn("Edit src/app.py", rendered)
        self.assertIn("12ms", rendered)
        self.assertNotIn("secret body", rendered)

    def test_approval_summary_hides_file_content(self) -> None:
        rendered = _safe_argument_summary(
            {"path": "main.py", "content": "private source text", "new_text": "replacement"}
        )
        self.assertIn("<19 characters>", rendered)
        self.assertIn("<11 characters>", rendered)
        self.assertNotIn("private source text", rendered)


if __name__ == "__main__":
    unittest.main()
