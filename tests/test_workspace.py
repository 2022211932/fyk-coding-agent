from pathlib import Path
import tempfile
import unittest

from fyk_agent.workspace import Workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resolves_normal_relative_path(self) -> None:
        resolved = self.workspace.resolve("src/app.py")
        self.assertEqual(resolved, self.root / "src" / "app.py")

    def test_rejects_parent_escape(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.workspace.resolve("../outside.txt")

    def test_rejects_absolute_path(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.workspace.resolve(str(self.root / "file.txt"))

    def test_rejects_credentials_and_internal_paths(self) -> None:
        for value in [".env", ".env.local", "keys/private.pem", ".git/config", ".fyk-agent/events.jsonl"]:
            with self.subTest(value=value), self.assertRaises(WorkspaceError):
                self.workspace.resolve(value)

    def test_rejects_symlink_escape_when_supported(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir(exist_ok=True)
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        try:
            with self.assertRaises(WorkspaceError):
                self.workspace.resolve("link/secret.txt")
        finally:
            link.unlink(missing_ok=True)
            outside.rmdir()


if __name__ == "__main__":
    unittest.main()

