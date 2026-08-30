import unittest

from fyk_agent.planning import PlanTracker


class PlanTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = PlanTracker()

    def update(self, steps: list[dict]) -> dict:
        return self.tracker.update({"summary": "Repair and verify", "steps": steps})

    def test_only_one_step_can_be_in_progress(self) -> None:
        result = self.update(
            [
                {"id": "inspect", "title": "Inspect", "kind": "inspect", "status": "in_progress"},
                {"id": "change", "title": "Change", "kind": "change", "status": "in_progress"},
            ]
        )
        self.assertFalse(result["ok"])
        self.assertIn("only one", result["error"])

    def test_completed_step_requires_known_compatible_evidence(self) -> None:
        evidence_id = self.tracker.register_evidence(
            "read_file", {"path": "app.py"}, {"ok": True, "path": "app.py"}, step=2
        )
        valid = self.update(
            [
                {
                    "id": "inspect",
                    "title": "Inspect implementation",
                    "kind": "inspect",
                    "status": "completed",
                    "evidence_ids": [evidence_id],
                }
            ]
        )
        self.assertTrue(valid["ok"], valid)
        self.assertEqual(valid["plan"]["evidence"][0]["summary"], "app.py")

        wrong_kind = PlanTracker()
        command_evidence = wrong_kind.register_evidence(
            "run_command", {"command": "python -m unittest"}, {"ok": True, "exit_code": 0}, step=2
        )
        invalid = wrong_kind.update(
            {
                "summary": "Inspect",
                "steps": [
                    {
                        "id": "inspect",
                        "title": "Inspect implementation",
                        "kind": "inspect",
                        "status": "completed",
                        "evidence_ids": [command_evidence],
                    }
                ],
            }
        )
        self.assertFalse(invalid["ok"])
        self.assertIn("requires successful evidence", invalid["error"])

    def test_failed_command_cannot_prove_verification(self) -> None:
        evidence_id = self.tracker.register_evidence(
            "run_command",
            {"command": "python -m unittest"},
            {"ok": False, "exit_code": 1},
            step=3,
        )
        result = self.update(
            [
                {
                    "id": "verify",
                    "title": "Run tests",
                    "kind": "verify",
                    "status": "completed",
                    "evidence_ids": [evidence_id],
                }
            ]
        )
        self.assertFalse(result["ok"])

    def test_successful_non_verification_command_cannot_prove_tests(self) -> None:
        evidence_id = self.tracker.register_evidence(
            "run_command",
            {"command": "echo tests passed"},
            {"ok": True, "exit_code": 0},
            step=3,
        )
        result = self.update(
            [
                {
                    "id": "verify",
                    "title": "Run tests",
                    "kind": "verify",
                    "status": "completed",
                    "evidence_ids": [evidence_id],
                }
            ]
        )
        self.assertFalse(result["ok"])

    def test_completed_steps_cannot_be_reopened(self) -> None:
        evidence_id = self.tracker.register_evidence(
            "write_file", {"path": "app.py"}, {"ok": True, "path": "app.py"}, step=2
        )
        self.assertTrue(
            self.update(
                [
                    {
                        "id": "change",
                        "title": "Change app",
                        "kind": "change",
                        "status": "completed",
                        "evidence_ids": [evidence_id],
                    }
                ]
            )["ok"]
        )
        reopened = self.update(
            [{"id": "change", "title": "Change app", "kind": "change", "status": "pending"}]
        )
        self.assertFalse(reopened["ok"])
        self.assertIn("cannot be removed or reopened", reopened["error"])

    def test_blocked_step_requires_note_and_is_terminal(self) -> None:
        missing_note = self.update(
            [{"id": "blocked", "title": "Unavailable", "kind": "other", "status": "blocked"}]
        )
        self.assertFalse(missing_note["ok"])
        result = self.update(
            [
                {
                    "id": "blocked",
                    "title": "Unavailable",
                    "kind": "other",
                    "status": "blocked",
                    "note": "Required service is offline",
                }
            ]
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["plan"]["terminal"])
        self.assertTrue(result["plan"]["blocked"])


if __name__ == "__main__":
    unittest.main()
