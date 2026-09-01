from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fyk_agent.engineering import EngineeringError, EngineeringWorkflow, PHASES, SKILLS
from fyk_agent.planning import Evidence


REQUIREMENTS = [
    {
        "id": "FR-001",
        "title": "用户登录",
        "kind": "functional",
        "description": "注册用户可以使用账号登录。",
        "acceptance_criteria": ["正确凭据进入系统", "错误凭据显示明确提示"],
    },
    {
        "id": "NFR-001",
        "title": "响应时间",
        "kind": "non_functional",
        "description": "正常负载下登录响应及时。",
        "acceptance_criteria": ["95% 请求在 500ms 内完成"],
    },
]


class EngineeringWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workflow = EngineeringWorkflow(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builtin_skills_cover_complete_lifecycle(self) -> None:
        self.assertEqual(tuple(skill["phase"] for skill in SKILLS), PHASES)
        self.assertEqual(self.workflow.payload()["active_skill"]["id"], "requirements-analysis")

    def test_requirements_are_persisted_and_generate_documents(self) -> None:
        result = self.workflow.update(
            {
                "action": "define_requirements",
                "project_title": "示例系统",
                "requirements": REQUIREMENTS,
            },
            {},
        )
        self.assertTrue(result["ok"])
        self.assertTrue((self.root / ".yukai/engineering/requirements.md").is_file())
        restored = EngineeringWorkflow(self.root).payload()
        self.assertEqual(restored["project_title"], "示例系统")
        self.assertEqual(len(restored["requirements"]), 2)
        self.assertFalse(restored["phases"][0]["gate"]["passed"])

    def test_user_decision_controls_requirements_gate(self) -> None:
        self.workflow.update({"action": "define_requirements", "requirements": REQUIREMENTS}, {})
        question = {
            "question_id": "baseline-1",
            "decision_key": "requirements_baseline",
            "question": "是否确认当前需求基线？",
            "reason": "设计阶段需要稳定的需求范围。",
            "options": [
                {"id": "approve", "label": "确认"},
                {"id": "revise", "label": "继续修改"},
            ],
        }
        requested = self.workflow.request_user_input(question)
        self.assertTrue(requested["awaiting_user"])
        self.workflow.answer_question("baseline-1", option_id="revise")
        rejected = self.workflow.update(
            {"action": "advance_phase", "target_phase": "design"}, {}
        )
        self.assertFalse(rejected["ok"])

        self.workflow.request_user_input({**question, "question_id": "baseline-2"})
        self.workflow.answer_question("baseline-2", option_id="approve")
        accepted = self.workflow.update(
            {"action": "advance_phase", "target_phase": "design"}, {}
        )
        self.assertTrue(accepted["ok"])
        self.assertEqual(self.workflow.payload()["phase"], "design")

    def test_traceability_links_require_compatible_evidence(self) -> None:
        self.workflow.update({"action": "define_requirements", "requirements": REQUIREMENTS}, {})
        self.workflow.request_user_input(
            {
                "question_id": "baseline",
                "decision_key": "requirements_baseline",
                "question": "确认需求？",
                "reason": "进入设计质量门。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.workflow.answer_question("baseline", option_id="approve")
        self.workflow.update({"action": "advance_phase", "target_phase": "design"}, {})
        self.workflow.update(
            {
                "action": "define_design",
                "modules": [
                    {
                        "id": "MOD-001",
                        "name": "认证模块",
                        "responsibility": "实现登录和性能约束。",
                        "requirement_ids": ["FR-001", "NFR-001"],
                        "interfaces": ["login(credentials)"],
                    }
                ],
            },
            {},
        )
        self.workflow.update({"action": "advance_phase", "target_phase": "implementation"}, {})
        bad = Evidence("read-1", "read_file", True, "read main.py", 1)
        result = self.workflow.update(
            {
                "action": "link_implementation",
                "links": [{"requirement_id": "FR-001", "path": "main.py", "evidence_id": "read-1"}],
            },
            {"read-1": bad},
        )
        self.assertFalse(result["ok"])

        change = Evidence("edit-1", "edit_file", True, "edited main.py", 2)
        linked = self.workflow.update(
            {
                "action": "link_implementation",
                "links": [
                    {"requirement_id": "FR-001", "path": "main.py", "evidence_id": "edit-1"},
                    {"requirement_id": "NFR-001", "path": "main.py", "evidence_id": "edit-1"},
                ],
            },
            {"edit-1": change},
        )
        self.assertTrue(linked["ok"])
        self.assertTrue(
            self.workflow.update(
                {"action": "advance_phase", "target_phase": "verification"}, {}
            )["ok"]
        )

        command = Evidence("cmd-1", "run_command", True, "ran pytest", 3, verification=True)
        verified = self.workflow.update(
            {
                "action": "link_tests",
                "links": [
                    {
                        "requirement_id": "FR-001",
                        "command": "pytest",
                        "evidence_id": "cmd-1",
                        "evidence_kind": "unit_test",
                        "claim": "登录成功和错误凭据均由测试断言覆盖",
                        "criterion_indices": [1, 2],
                    },
                    {
                        "requirement_id": "NFR-001",
                        "command": "pytest",
                        "evidence_id": "cmd-1",
                        "evidence_kind": "performance_test",
                        "claim": "验证命令检查响应约束",
                        "criterion_indices": [1],
                    },
                ],
            },
            {"cmd-1": command},
        )
        self.assertTrue(verified["ok"])
        matrix = (self.root / ".yukai/engineering/traceability.md").read_text(encoding="utf-8")
        self.assertIn("FR-001", matrix)
        self.assertIn("main.py", matrix)
        self.assertIn("pytest", matrix)

    def test_stale_question_answer_is_rejected(self) -> None:
        with self.assertRaises(EngineeringError):
            self.workflow.answer_question("missing", option_id="approve")

    def test_persistent_evidence_auto_advances_and_invalidates_stale_tests(self) -> None:
        self.workflow.update({"action": "define_requirements", "requirements": REQUIREMENTS}, {})
        self.workflow.request_user_input(
            {
                "question_id": "baseline-auto",
                "decision_key": "requirements_baseline",
                "question": "确认需求基线？",
                "reason": "进入设计阶段。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.workflow.answer_question("baseline-auto", option_id="approve")
        self.assertEqual(self.workflow.payload()["phase"], "design")
        self.workflow.update(
            {
                "action": "define_design",
                "modules": [
                    {
                        "id": "MOD-001",
                        "name": "认证模块",
                        "responsibility": "覆盖登录及其响应约束。",
                        "requirement_ids": ["FR-001", "NFR-001"],
                        "interfaces": ["login(credentials)"],
                    }
                ],
            },
            {},
        )
        self.assertEqual(self.workflow.payload()["phase"], "implementation")

        (self.root / "main.py").write_text("def login(): return True\n", encoding="utf-8")
        change = Evidence("change-persisted", "write_file", True, "main.py", 1)
        self.workflow.record_evidence(
            change, {"path": "main.py"}, {"ok": True, "path": "main.py"}
        )
        restored = EngineeringWorkflow(self.root)
        linked = restored.update(
            {
                "action": "link_implementation",
                "links": [
                    {"requirement_id": "FR-001", "path": "main.py", "evidence_id": "change-persisted"},
                    {"requirement_id": "NFR-001", "path": "main.py", "evidence_id": "change-persisted"},
                ],
            },
            {},
        )
        self.assertTrue(linked["ok"])
        self.assertEqual(restored.payload()["phase"], "verification")

        command = Evidence("test-persisted", "run_command", True, "pytest · exit 0", 2, verification=True)
        restored.record_evidence(
            command, {"command": "pytest"}, {"ok": True, "exit_code": 0}
        )
        verified = EngineeringWorkflow(self.root).update(
            {
                "action": "link_tests",
                "links": [
                    {
                        "requirement_id": "FR-001",
                        "command": "pytest",
                        "evidence_id": "test-persisted",
                        "evidence_kind": "unit_test",
                        "claim": "覆盖正确和错误凭据",
                        "criterion_indices": [1, 2],
                    },
                    {
                        "requirement_id": "NFR-001",
                        "command": "pytest",
                        "evidence_id": "test-persisted",
                        "evidence_kind": "performance_test",
                        "claim": "检查响应时间约束",
                        "criterion_indices": [1],
                    },
                ],
            },
            {},
        )
        self.assertTrue(verified["ok"])
        self.assertEqual(verified["engineering"]["phase"], "acceptance")

        question = EngineeringWorkflow(self.root).request_user_input(
            {
                "question_id": "accept-review",
                "decision_key": "project_acceptance",
                "question": "是否验收？",
                "reason": "全部质量门已满足。",
                "options": [{"id": "approve", "label": "验收"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.assertEqual(question["question"]["review_summary"]["stale_evidence"], 0)

        (self.root / "main.py").write_text("def login(): return False\n", encoding="utf-8")
        stale = EngineeringWorkflow(self.root).payload()
        self.assertEqual(stale["phase"], "verification")
        self.assertFalse(next(item for item in stale["phases"] if item["id"] == "verification")["gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
