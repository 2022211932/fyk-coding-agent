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
                "assumptions": ["默认仅支持网页登录"],
                "requirements": REQUIREMENTS,
            },
            {},
        )
        self.assertTrue(result["ok"])
        self.assertTrue((self.root / ".yukai/engineering/requirements.md").is_file())
        restored = EngineeringWorkflow(self.root).payload()
        self.assertEqual(restored["project_title"], "示例系统")
        self.assertEqual(len(restored["requirements"]), 2)
        self.assertEqual(restored["assumptions"], ["默认仅支持网页登录"])
        requirements_document = (
            self.root / ".yukai/engineering/requirements.md"
        ).read_text(encoding="utf-8")
        self.assertIn("待确认的默认决策与假设", requirements_document)
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
        baseline = requested["question"]["baseline_review"]
        self.assertEqual([item["id"] for item in baseline["requirements"]], ["FR-001", "NFR-001"])
        self.assertEqual(baseline["requirements"][0]["acceptance_criteria"], REQUIREMENTS[0]["acceptance_criteria"])
        self.assertEqual(len(baseline["digest"]), 64)
        self.workflow.answer_question(
            "baseline-1", option_id="revise", answer="补充登录失败时的错误码要求"
        )
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

    def test_changing_requirements_invalidates_pending_baseline_card(self) -> None:
        self.workflow.update({"action": "define_requirements", "requirements": REQUIREMENTS}, {})
        self.workflow.request_user_input(
            {
                "question_id": "old-baseline",
                "decision_key": "requirements_baseline",
                "question": "确认需求？",
                "reason": "进入设计。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        changed = [dict(REQUIREMENTS[0]), dict(REQUIREMENTS[1])]
        changed[0]["title"] = "更新后的用户登录"
        self.workflow.update({"action": "define_requirements", "requirements": changed}, {})

        self.assertIsNone(self.workflow.payload()["pending_question"])
        with self.assertRaisesRegex(EngineeringError, "no longer pending"):
            self.workflow.answer_question("old-baseline", option_id="approve")

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
        candidate = Evidence("edit-candidate", "edit_file", True, "edited candidate.py", 1)
        result = self.workflow.update(
            {
                "action": "link_implementation",
                "links": [{"requirement_id": "FR-001", "path": "main.py", "evidence_id": "read-1"}],
            },
            {"read-1": bad, "edit-candidate": candidate},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["actual_evidence"]["tool"], "read_file")
        self.assertIn("actual type read_file", result["error"])
        self.assertEqual(result["candidate_evidence"][0]["id"], "edit-candidate")

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

    def test_revision_choice_requires_free_text(self) -> None:
        self.workflow.update({"action": "define_requirements", "requirements": REQUIREMENTS}, {})
        requested = self.workflow.request_user_input(
            {
                "question_id": "baseline-free-text",
                "decision_key": "requirements_baseline",
                "question": "确认需求？",
                "reason": "进入设计。",
                "options": [
                    {"id": "approve", "label": "确认"},
                    {"id": "revise", "label": "需要修改"},
                ],
            }
        )
        revise = next(item for item in requested["question"]["options"] if item["id"] == "revise")
        self.assertTrue(revise["requires_input"])
        with self.assertRaisesRegex(EngineeringError, "free-text"):
            self.workflow.answer_question("baseline-free-text", option_id="revise")
        answered = self.workflow.answer_question(
            "baseline-free-text", option_id="revise", answer="增加审计日志要求"
        )
        self.assertEqual(answered["decision"]["answer"], "增加审计日志要求")

    def test_completed_project_requires_replacement_decision_before_rollback(self) -> None:
        self.workflow._state["phase"] = "acceptance"
        self.workflow._state["status"] = "completed"
        self.workflow._state["project_title"] = "旧项目"
        blocked = self.workflow.update(
            {"action": "advance_phase", "target_phase": "requirements"}, {}
        )
        self.assertFalse(blocked["ok"])
        self.assertIn("completed_project_change", blocked["error"])

        requested = self.workflow.request_user_input(
            {
                "question_id": "replace-project",
                "decision_key": "completed_project_change",
                "question": "如何处理当前已验收项目？",
                "reason": "新请求可能替换现有项目。",
                "options": [
                    {"id": "modify_current", "label": "修改当前项目"},
                    {"id": "replace_current", "label": "替换当前项目"},
                    {"id": "new_workspace", "label": "使用新工作区"},
                ],
            }
        )
        self.assertEqual(requested["question"]["workspace_review"]["project_title"], "旧项目")
        self.workflow.answer_question("replace-project", option_id="replace_current")
        rolled_back = self.workflow.update(
            {"action": "advance_phase", "target_phase": "requirements"}, {}
        )
        self.assertTrue(rolled_back["ok"])

    def test_new_workspace_decision_cannot_replace_completed_project(self) -> None:
        self.workflow._state["phase"] = "acceptance"
        self.workflow._state["status"] = "completed"
        self.workflow.request_user_input(
            {
                "question_id": "keep-project",
                "decision_key": "completed_project_change",
                "question": "如何处理当前项目？",
                "reason": "避免覆盖。",
                "options": [
                    {"id": "modify_current", "label": "修改当前项目"},
                    {"id": "replace_current", "label": "替换当前项目"},
                    {"id": "new_workspace", "label": "使用新工作区"},
                ],
            }
        )
        self.workflow.answer_question("keep-project", option_id="new_workspace")
        result = self.workflow.update(
            {"action": "advance_phase", "target_phase": "requirements"}, {}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(self.workflow.payload()["status"], "completed")

    def test_documented_test_count_must_match_latest_successful_run(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "TESTING.md").write_text("当前 17 个测试全部通过。\n", encoding="utf-8")
        evidence = Evidence(
            "tests-20", "run_command", True, "python -m unittest · exit 0", 1, verification=True
        )
        self.workflow.record_evidence(
            evidence,
            {"command": "python -m unittest"},
            {"ok": True, "exit_code": 0, "stdout": "Ran 20 tests in 0.2s\nOK"},
        )
        missing = self.workflow._documentation_consistency_missing()
        self.assertTrue(any("声明 [17]，实际 20" in item for item in missing))


if __name__ == "__main__":
    unittest.main()
