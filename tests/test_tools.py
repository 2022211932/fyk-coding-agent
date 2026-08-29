import os
from pathlib import Path
import sys
import tempfile
import unittest

from fyk_agent.tools import ToolRegistry
from fyk_agent.workspace import Workspace


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = ToolRegistry(Workspace(self.root), approve=lambda _name, _args: True)

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


if __name__ == "__main__":
    unittest.main()

