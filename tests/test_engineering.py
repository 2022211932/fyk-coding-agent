from copy import deepcopy
import json
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

USE_CASE_MODEL = {
    "actors": [
        {
            "id": "ACT-001",
            "name": "注册用户",
            "description": "使用账号凭据登录系统的外部参与者。",
        }
    ],
    "use_cases": [
        {
            "id": "UC-001",
            "name": "用户登录",
            "goal": "注册用户通过凭据进入系统，错误凭据得到明确提示。",
            "actor_ids": ["ACT-001"],
            "preconditions": ["用户已经注册"],
            "main_flow": ["用户提交正确凭据", "系统验证凭据", "系统允许用户进入"],
            "alternative_flows": [
                {
                    "id": "ALT-001",
                    "condition": "凭据错误",
                    "steps": ["系统拒绝登录", "系统显示明确错误提示"],
                }
            ],
            "postconditions": ["成功时建立登录状态，失败时不建立登录状态"],
            "acceptance_links": [
                {"requirement_id": "FR-001", "criterion_indices": [1, 2]}
            ],
        }
    ],
    "use_case_relationships": [],
}


def requirements_payload(
    requirements: list[dict[str, object]] = REQUIREMENTS, **extra: object
) -> dict[str, object]:
    return {
        "action": "define_requirements",
        "requirements": requirements,
        **deepcopy(USE_CASE_MODEL),
        **extra,
    }

DESIGN_ARTIFACTS = {
    "uml_classes": [
        {
            "id": "CLS-001",
            "name": "AuthService",
            "stereotype": "service",
            "attributes": [],
            "methods": ["login(credentials)"],
            "requirement_ids": ["FR-001", "NFR-001"],
        }
    ],
    "uml_relationships": [],
    "sequences": [
        {
            "id": "SEQ-001",
            "name": "用户登录",
            "requirement_ids": ["FR-001"],
            "participants": ["用户", "认证服务"],
            "steps": [{"from": "用户", "to": "认证服务", "message": "提交登录凭据"}],
        }
    ],
    "process_flows": [
        {
            "id": "FLOW-001",
            "name": "用户登录业务流程",
            "requirement_ids": ["FR-001"],
            "direction": "TD",
            "nodes": [
                {"id": "START", "type": "start", "label": "开始"},
                {"id": "INPUT", "type": "input_output", "label": "用户提交登录凭据"},
                {"id": "VALID", "type": "decision", "label": "凭据是否正确"},
                {"id": "SUCCESS", "type": "process", "label": "进入系统"},
                {"id": "REJECT", "type": "process", "label": "显示错误提示"},
                {"id": "END", "type": "end", "label": "结束"},
            ],
            "edges": [
                {"from": "START", "to": "INPUT"},
                {"from": "INPUT", "to": "VALID"},
                {"from": "VALID", "to": "SUCCESS", "label": "是"},
                {"from": "VALID", "to": "REJECT", "label": "否"},
                {"from": "SUCCESS", "to": "END"},
                {"from": "REJECT", "to": "END"},
            ],
        }
    ],
    "domain_objects": [
        {
            "id": "DOM-001",
            "name": "用户",
            "kind": "entity",
            "description": "可以使用凭据登录的注册用户。",
            "business_rules": ["错误凭据不得进入系统"],
            "requirement_ids": ["FR-001"],
        }
    ],
}


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
            requirements_payload(
                project_title="示例系统",
                assumptions=["默认仅支持网页登录"],
            ),
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
        self.assertIn("# 用例模型", requirements_document)
        self.assertIn("UC-001 用户登录", requirements_document)
        self.assertIn("FR-001 验收标准 1,2", requirements_document)
        self.assertFalse(restored["phases"][0]["gate"]["passed"])

    def test_user_decision_controls_requirements_gate(self) -> None:
        self.workflow.update(requirements_payload(), {})
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
        self.assertEqual(baseline["actors"][0]["id"], "ACT-001")
        self.assertEqual(baseline["use_cases"][0]["id"], "UC-001")
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

    def test_requirements_gate_reports_uncovered_functional_acceptance_criteria(self) -> None:
        payload = requirements_payload()
        payload["use_cases"][0]["acceptance_links"][0]["criterion_indices"] = [1]
        defined = self.workflow.update(payload, {})
        self.assertTrue(defined["ok"])

        gate = self.workflow.payload()["phases"][0]["gate"]
        self.assertFalse(gate["passed"])
        self.assertIn("FR-001 的验收标准缺少用例覆盖：2", gate["missing"])
        requested = self.workflow.request_user_input(
            {
                "question_id": "incomplete-use-case-baseline",
                "decision_key": "requirements_baseline",
                "question": "是否确认需求？",
                "reason": "进入设计前确认。",
                "options": [
                    {"id": "approve", "label": "确认"},
                    {"id": "revise", "label": "修改"},
                ],
            }
        )
        self.assertFalse(requested["ok"])
        self.assertIn("FR-001 的验收标准缺少用例覆盖：2", requested["error"])

    def test_use_case_rejects_invalid_acceptance_criterion_index(self) -> None:
        payload = requirements_payload()
        payload["use_cases"][0]["acceptance_links"][0]["criterion_indices"] = [3]
        result = self.workflow.update(payload, {})

        self.assertFalse(result["ok"])
        self.assertIn("must be between 1 and 2", result["error"])

    def test_version_three_project_remains_compatible_without_use_case_model(self) -> None:
        self.workflow.update(requirements_payload(), {})
        state_path = self.root / ".yukai/engineering/project.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy["version"] = 3
        for key in ("actors", "use_cases", "use_case_relationships", "use_case_model_required"):
            legacy.pop(key, None)
        state_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

        restored = EngineeringWorkflow(self.root).payload()
        self.assertFalse(restored["use_case_model_required"])
        self.assertEqual(restored["actors"], [])
        self.assertFalse(any("用例" in item for item in restored["phases"][0]["gate"]["missing"]))

    def test_changing_requirements_invalidates_pending_baseline_card(self) -> None:
        self.workflow.update(requirements_payload(), {})
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
        self.workflow.update(requirements_payload(changed), {})

        self.assertIsNone(self.workflow.payload()["pending_question"])
        with self.assertRaisesRegex(EngineeringError, "no longer pending"):
            self.workflow.answer_question("old-baseline", option_id="approve")

    def test_complete_design_requires_explicit_baseline_approval(self) -> None:
        self.workflow.update(requirements_payload(), {})
        self.workflow.request_user_input(
            {
                "question_id": "requirements-for-design",
                "decision_key": "requirements_baseline",
                "question": "确认需求？",
                "reason": "进入设计。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.workflow.answer_question("requirements-for-design", option_id="approve")
        modules = [
            {
                "id": "MOD-001",
                "name": "认证模块",
                "responsibility": "覆盖登录及响应约束。",
                "requirement_ids": ["FR-001", "NFR-001"],
                "interfaces": ["login(credentials)"],
            }
        ]
        self.workflow.update({"action": "define_design", "modules": modules}, {})
        incomplete = self.workflow.request_user_input(
            {
                "question_id": "incomplete-design",
                "decision_key": "design_baseline",
                "question": "确认设计？",
                "reason": "进入实现。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.assertFalse(incomplete["ok"])
        self.assertIn("UML", incomplete["error"])

        self.workflow.update(
            {"action": "define_design", "modules": modules, **DESIGN_ARTIFACTS}, {}
        )
        blocked = self.workflow.update(
            {"action": "advance_phase", "target_phase": "implementation"}, {}
        )
        self.assertFalse(blocked["ok"])
        self.assertIn("用户确认设计基线", blocked["error"])
        requested = self.workflow.request_user_input(
            {
                "question_id": "complete-design",
                "decision_key": "design_baseline",
                "question": "确认 UML、时序图与领域模型？",
                "reason": "进入实现。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.assertEqual(len(requested["question"]["design_review"]["uml_classes"]), 1)
        self.assertEqual(len(requested["question"]["design_review"]["process_flows"]), 1)
        self.workflow.answer_question("complete-design", option_id="approve")
        self.assertEqual(self.workflow.payload()["phase"], "implementation")

    def test_process_flow_validates_branches_coverage_and_design_document(self) -> None:
        self.workflow.update(requirements_payload(), {})
        self.workflow.request_user_input(
            {
                "question_id": "flow-requirements",
                "decision_key": "requirements_baseline",
                "question": "确认需求？",
                "reason": "进入设计。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.workflow.answer_question("flow-requirements", option_id="approve")
        self.workflow.update({"action": "advance_phase", "target_phase": "design"}, {})
        modules = [
            {
                "id": "MOD-001",
                "name": "认证模块",
                "responsibility": "完成用户登录。",
                "requirement_ids": ["FR-001", "NFR-001"],
                "interfaces": ["login(credentials)"],
            }
        ]
        invalid = deepcopy(DESIGN_ARTIFACTS)
        invalid["process_flows"][0]["edges"][2].pop("label")
        rejected = self.workflow.update(
            {"action": "define_design", "modules": modules, **invalid}, {}
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("labeled branches", rejected["error"])

        uncovered = deepcopy(DESIGN_ARTIFACTS)
        uncovered["process_flows"][0]["requirement_ids"] = ["NFR-001"]
        self.assertTrue(
            self.workflow.update(
                {"action": "define_design", "modules": modules, **uncovered}, {}
            )["ok"]
        )
        baseline = self.workflow.request_user_input(
            {
                "question_id": "flow-design-incomplete",
                "decision_key": "design_baseline",
                "question": "确认设计？",
                "reason": "检查流程覆盖。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.assertFalse(baseline["ok"])
        self.assertIn("业务流程图覆盖功能需求", baseline["error"])

        self.assertTrue(
            self.workflow.update(
                {"action": "define_design", "modules": modules, **DESIGN_ARTIFACTS}, {}
            )["ok"]
        )
        design_document = (self.root / ".yukai" / "engineering" / "design.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("# 系统业务流程图", design_document)
        self.assertIn("flowchart TD", design_document)

    def test_traceability_links_require_compatible_evidence(self) -> None:
        self.workflow.update(requirements_payload(), {})
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
                **DESIGN_ARTIFACTS,
            },
            {},
        )
        design_question = self.workflow.request_user_input(
            {
                "question_id": "design-baseline",
                "decision_key": "design_baseline",
                "question": "是否确认设计基线？",
                "reason": "实现前必须确认 UML、时序图与领域模型。",
                "options": [{"id": "approve", "label": "确认设计"}, {"id": "revise", "label": "修改设计"}],
            }
        )
        self.assertIn("design_review", design_question["question"])
        self.workflow.answer_question("design-baseline", option_id="approve")
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

        command = Evidence(
            "cmd-1",
            "run_command",
            True,
            "python -m unittest -v · exit 0",
            3,
            verification=True,
        )
        self.workflow.record_evidence(
            command,
            {"command": "python -m unittest -v"},
            {
                "ok": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": (
                    "test_login_public (test_auth.AuthTests.test_login_public) ... ok\n"
                    "test_login_branches (test_auth.AuthTests.test_login_branches) ... ok\n"
                    "test_login_performance (test_auth.AuthTests.test_login_performance) ... ok\n"
                    "Ran 3 tests in 0.010s\nOK"
                ),
            },
        )
        inspection = Evidence("inspect-1", "read_file", True, "read performance notes", 4)
        verified = self.workflow.update(
            {
                "action": "link_tests",
                "links": [
                    {
                        "requirement_id": "FR-001",
                        "command": "python -m unittest -v",
                        "evidence_id": "cmd-1",
                        "evidence_kind": "integration_test",
                        "test_method": "black_box",
                        "test_level": "integration",
                        "claim": "test_login_public 通过公开接口覆盖正确和错误凭据",
                        "criterion_indices": [1, 2],
                        "test_case_ids": ["test_login_public"],
                    },
                    {
                        "requirement_id": "FR-001",
                        "command": "python -m unittest -v",
                        "evidence_id": "cmd-1",
                        "evidence_kind": "unit_test",
                        "test_method": "white_box",
                        "test_level": "unit",
                        "module_ids": ["MOD-001"],
                        "claim": "test_login_branches 直接覆盖认证模块内部成功和失败分支",
                        "criterion_indices": [1, 2],
                        "test_case_ids": ["test_login_branches"],
                    },
                    {
                        "requirement_id": "NFR-001",
                        "command": "python -m unittest -v",
                        "evidence_id": "cmd-1",
                        "evidence_kind": "performance_test",
                        "test_method": "black_box",
                        "test_level": "performance",
                        "claim": "test_login_performance 检查响应约束",
                        "criterion_indices": [1],
                        "test_case_ids": ["test_login_performance"],
                    },
                    {
                        "requirement_id": "NFR-001",
                        "command": "read performance notes",
                        "evidence_id": "inspect-1",
                        "evidence_kind": "inspection",
                        "claim": "检查性能约束说明",
                        "criterion_indices": [1],
                    },
                ],
            },
            {"cmd-1": command, "inspect-1": inspection},
        )
        self.assertTrue(verified["ok"])
        supporting = next(
            item for item in self.workflow.payload()["test_links"]
            if item["evidence_id"] == "inspect-1"
        )
        self.assertEqual(supporting["test_method"], "")
        self.assertEqual(supporting["test_level"], "static")
        matrix = (self.root / ".yukai/engineering/traceability.md").read_text(encoding="utf-8")
        self.assertIn("FR-001", matrix)
        self.assertIn("main.py", matrix)
        self.assertIn("python -m unittest -v", matrix)

    def test_stale_question_answer_is_rejected(self) -> None:
        with self.assertRaises(EngineeringError):
            self.workflow.answer_question("missing", option_id="approve")

    def test_persistent_evidence_auto_advances_and_invalidates_stale_tests(self) -> None:
        self.workflow.update(requirements_payload(), {})
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
                **DESIGN_ARTIFACTS,
            },
            {},
        )
        self.assertEqual(self.workflow.payload()["phase"], "design")
        self.workflow.request_user_input(
            {
                "question_id": "design-auto",
                "decision_key": "design_baseline",
                "question": "确认设计基线？",
                "reason": "进入实现阶段。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.workflow.answer_question("design-auto", option_id="approve")
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

        command = Evidence(
            "test-persisted",
            "run_command",
            True,
            "python -m unittest -v · exit 0",
            2,
            verification=True,
        )
        restored.record_evidence(
            command,
            {"command": "python -m unittest -v"},
            {
                "ok": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": (
                    "test_login_public (test_auth.AuthTests.test_login_public) ... ok\n"
                    "test_login_branches (test_auth.AuthTests.test_login_branches) ... ok\n"
                    "test_login_performance (test_auth.AuthTests.test_login_performance) ... ok\n"
                    "Ran 3 tests in 0.010s\nOK"
                ),
            },
        )
        verified = EngineeringWorkflow(self.root).update(
            {
                "action": "link_tests",
                "links": [
                    {
                        "requirement_id": "FR-001",
                        "command": "python -m unittest -v",
                        "evidence_id": "test-persisted",
                        "evidence_kind": "integration_test",
                        "test_method": "black_box",
                        "test_level": "integration",
                        "claim": "test_login_public 覆盖正确和错误凭据",
                        "criterion_indices": [1, 2],
                        "test_case_ids": ["test_login_public"],
                    },
                    {
                        "requirement_id": "FR-001",
                        "command": "python -m unittest -v",
                        "evidence_id": "test-persisted",
                        "evidence_kind": "unit_test",
                        "test_method": "white_box",
                        "test_level": "unit",
                        "module_ids": ["MOD-001"],
                        "claim": "test_login_branches 覆盖认证模块内部成功和失败分支",
                        "criterion_indices": [1, 2],
                        "test_case_ids": ["test_login_branches"],
                    },
                    {
                        "requirement_id": "NFR-001",
                        "command": "python -m unittest -v",
                        "evidence_id": "test-persisted",
                        "evidence_kind": "performance_test",
                        "test_method": "black_box",
                        "test_level": "performance",
                        "claim": "test_login_performance 检查响应时间约束",
                        "criterion_indices": [1],
                        "test_case_ids": ["test_login_performance"],
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
        accepted = EngineeringWorkflow(self.root).answer_question(
            "accept-review", option_id="approve"
        )
        self.assertEqual(accepted["engineering"]["status"], "completed")
        self.assertTrue(EngineeringWorkflow(self.root).is_completed)
        state_path = self.root / ".yukai/engineering/project.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy["version"] = 4
        legacy["status"] = "active"
        legacy.pop("structured_test_strategy_required", None)
        state_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        migrated = EngineeringWorkflow(self.root).payload()
        self.assertFalse(migrated["test_strategy_audit"]["required"])
        self.assertEqual(migrated["status"], "completed")

        (self.root / "main.py").write_text("def login(): return False\n", encoding="utf-8")
        stale = EngineeringWorkflow(self.root).payload()
        self.assertEqual(stale["phase"], "verification")
        self.assertEqual(stale["status"], "active")
        self.assertFalse(next(item for item in stale["phases"] if item["id"] == "verification")["gate"]["passed"])

    def test_module_and_changed_file_traceability_block_acceptance_until_complete(self) -> None:
        self.workflow.update(requirements_payload(), {})
        self.workflow.request_user_input(
            {
                "question_id": "trace-requirements",
                "decision_key": "requirements_baseline",
                "question": "确认需求？",
                "reason": "进入设计。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.workflow.answer_question("trace-requirements", option_id="approve")
        self.workflow.update(
            {
                "action": "define_design",
                "modules": [
                    {
                        "id": "MOD-001",
                        "name": "核心模块",
                        "responsibility": "实现登录与响应约束。",
                        "requirement_ids": ["FR-001", "NFR-001"],
                        "interfaces": ["login(credentials)"],
                    },
                    {
                        "id": "MOD-002",
                        "name": "性能配置模块",
                        "responsibility": "提供响应时间配置。",
                        "requirement_ids": ["NFR-001"],
                        "interfaces": ["load_limits()"],
                    },
                ],
                **DESIGN_ARTIFACTS,
            },
            {},
        )
        self.workflow.request_user_input(
            {
                "question_id": "trace-design",
                "decision_key": "design_baseline",
                "question": "确认设计？",
                "reason": "进入实现。",
                "options": [{"id": "approve", "label": "确认"}, {"id": "revise", "label": "修改"}],
            }
        )
        self.workflow.answer_question("trace-design", option_id="approve")

        (self.root / "main.py").write_text("def login(): return True\n", encoding="utf-8")
        (self.root / "limits.py").write_text("MAX_MS = 500\n", encoding="utf-8")
        main_evidence = Evidence("write-main", "write_file", True, "main.py", 1)
        limits_evidence = Evidence("write-limits", "write_file", True, "limits.py", 2)
        self.workflow.record_evidence(
            main_evidence, {"path": "main.py"}, {"ok": True, "path": "main.py"}
        )
        self.workflow.record_evidence(
            limits_evidence, {"path": "limits.py"}, {"ok": True, "path": "limits.py"}
        )

        invalid = self.workflow.update(
            {
                "action": "link_implementation",
                "links": [{
                    "requirement_id": "FR-001",
                    "module_ids": ["MOD-002"],
                    "path": "main.py",
                    "evidence_id": "write-main",
                }],
            },
            {},
        )
        self.assertFalse(invalid["ok"])
        self.assertIn("do not own this requirement", invalid["error"])

        partial = self.workflow.update(
            {
                "action": "link_implementation",
                "links": [
                    {
                        "requirement_id": "FR-001",
                        "module_ids": ["MOD-001"],
                        "path": "main.py",
                        "evidence_id": "write-main",
                    },
                    {
                        "requirement_id": "NFR-001",
                        "module_ids": ["MOD-001"],
                        "path": "main.py",
                        "evidence_id": "write-main",
                    },
                ],
            },
            {},
        )
        self.assertTrue(partial["ok"])
        audit = self.workflow.payload()["implementation_audit"]
        self.assertEqual(audit["modules_completed"], 1)
        self.assertEqual(audit["incomplete_modules"][0]["id"], "MOD-002")
        self.assertEqual(audit["untracked_files"], ["limits.py"])
        self.assertEqual(self.workflow.payload()["phase"], "implementation")

        self.workflow._state["phase"] = "acceptance"
        self.workflow._state["status"] = "completed"
        self.workflow._state["decisions"].append(
            {"key": "project_acceptance", "option_id": "accept", "option_label": "验收"}
        )
        reopened = self.workflow.payload()
        self.assertEqual(reopened["phase"], "implementation")
        self.assertEqual(reopened["status"], "active")
        self.assertFalse(any(item["key"] == "project_acceptance" for item in reopened["decisions"]))

        completed = self.workflow.update(
            {
                "action": "link_implementation",
                "links": [{
                    "requirement_id": "NFR-001",
                    "module_ids": ["MOD-001", "MOD-002"],
                    "path": "limits.py",
                    "evidence_id": "write-limits",
                }],
            },
            {},
        )
        self.assertTrue(completed["ok"])
        final_audit = self.workflow.payload()["implementation_audit"]
        self.assertTrue(final_audit["passed"])
        self.assertEqual(final_audit["modules_completed"], 2)
        self.assertEqual(final_audit["untracked_files"], [])
        self.assertEqual(self.workflow.payload()["phase"], "verification")

    def test_revision_choice_requires_free_text(self) -> None:
        self.workflow.update(requirements_payload(), {})
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

    def test_verification_summary_counts_real_black_and_white_unittest_cases(self) -> None:
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_api.py").write_text(
            '"""黑盒测试：通过公开接口验证行为。"""\n\n'
            "import unittest\n\n"
            "class ApiTest(unittest.TestCase):\n"
            "    def test_create(self):\n"
            "        pass\n\n"
            "    def test_delete(self):\n"
            "        pass\n",
            encoding="utf-8",
        )
        (tests / "test_service.py").write_text(
            '"""白盒测试：直接验证业务分支。"""\n\n'
            "import unittest\n\n"
            "class ServiceTest(unittest.TestCase):\n"
            "    def test_validate(self):\n"
            "        pass\n",
            encoding="utf-8",
        )
        output = "\n".join(
            (
                "test_create (test_api.ApiTest.test_create) ... ok",
                "test_delete (test_api.ApiTest.test_delete) ... skipped 'not available'",
                "test_validate (test_service.ServiceTest.test_validate) ... ok",
                "",
                "----------------------------------------------------------------------",
                "Ran 3 tests in 0.125s",
                "",
                "OK (skipped=1)",
            )
        )
        evidence = Evidence(
            "tests-structured", "run_command", True, "python -m unittest -v · exit 0", 1,
            verification=True,
        )
        self.workflow.record_evidence(
            evidence,
            {"command": "python -m unittest discover -s tests -v"},
            {"ok": True, "exit_code": 0, "stdout": "", "stderr": output},
        )
        self.workflow._state["test_links"] = [
            {
                "requirement_id": "FR-001",
                "command": "python -m unittest discover -s tests -v",
                "evidence_kind": "integration_test",
                "test_method": "black_box",
                "test_level": "system",
                "claim": "test_create verifies creation",
                "criterion_indices": [1],
                "test_case_ids": ["test_create"],
            }
        ]

        summary = self.workflow._verification_summary()
        run = summary["latest_run"]
        self.assertEqual((run["total"], run["passed"], run["skipped"]), (3, 2, 1))
        self.assertEqual(run["black_box"]["total"], 2)
        self.assertEqual(run["white_box"]["total"], 1)
        self.assertEqual(run["unclassified"]["total"], 0)
        self.assertEqual(len(run["cases"]), 3)
        create = next(item for item in run["cases"] if item["name"] == "test_create")
        self.assertEqual(create["traces"][0]["requirement_id"], "FR-001")
        self.assertEqual(summary["dynamic_trace_links"], 1)

    def test_supporting_unittest_is_not_counted_as_business_white_box(self) -> None:
        self.workflow._state["requirements"] = [deepcopy(REQUIREMENTS[1])]
        self.workflow._state["phase"] = "verification"
        evidence = Evidence(
            "stdlib-run",
            "run_command",
            True,
            "python -m unittest -v · exit 0",
            1,
            verification=True,
        )
        self.workflow.record_evidence(
            evidence,
            {"command": "python -m unittest -v"},
            {
                "ok": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": (
                    "test_uses_stdlib_only (test_stdlib.DependencyTests.test_uses_stdlib_only) ... ok\n"
                    "Ran 1 test in 0.001s\nOK"
                ),
            },
        )
        linked = self.workflow.update(
            {
                "action": "link_tests",
                "links": [
                    {
                        "requirement_id": "NFR-001",
                        "command": "python -m unittest -v",
                        "evidence_id": "stdlib-run",
                        "evidence_kind": "supporting_test",
                        "test_level": "static",
                        "claim": "test_uses_stdlib_only 检查第三方依赖",
                        "criterion_indices": [1],
                        "test_case_ids": ["test_uses_stdlib_only"],
                    }
                ],
            },
            {},
        )
        self.assertTrue(linked["ok"])
        run = self.workflow.payload()["verification_summary"]["latest_run"]
        self.assertEqual(run["supporting"]["total"], 1)
        self.assertEqual(run["white_box"]["total"], 0)
        self.assertEqual(run["cases"][0]["method"], "supporting")

    def test_strategy_gate_requires_black_box_criteria_and_white_box_modules(self) -> None:
        (self.root / "main.py").write_text("def login(): return True\n", encoding="utf-8")
        self.workflow._state["requirements"] = [deepcopy(REQUIREMENTS[0])]
        self.workflow._state["design_modules"] = [
            {
                "id": "MOD-001",
                "name": "认证模块",
                "responsibility": "实现登录分支。",
                "requirement_ids": ["FR-001"],
                "interfaces": ["login(credentials)"],
                "dependencies": [],
            }
        ]
        self.workflow._state["implementation_links"] = [
            {
                "requirement_id": "FR-001",
                "module_ids": ["MOD-001"],
                "path": "main.py",
                "evidence_id": "write-main",
            }
        ]
        fingerprint = self.workflow._implementation_fingerprint()
        self.workflow._state["test_links"] = [
            {
                "requirement_id": "FR-001",
                "command": "python -m unittest -v",
                "evidence_id": "tests",
                "evidence_kind": "integration_test",
                "test_method": "black_box",
                "test_level": "integration",
                "module_ids": [],
                "claim": "公开登录行为",
                "criterion_indices": [1, 2],
                "test_case_ids": ["test_login"],
                "implementation_fingerprint": fingerprint,
            }
        ]
        missing_white = self.workflow._test_strategy_audit()
        self.assertFalse(missing_white["passed"])
        self.assertEqual(missing_white["missing_white_box_modules"], ["MOD-001"])

        self.workflow._state["test_links"].append(
            {
                "requirement_id": "FR-001",
                "command": "python -m unittest -v",
                "evidence_id": "tests",
                "evidence_kind": "unit_test",
                "test_method": "white_box",
                "test_level": "unit",
                "module_ids": ["MOD-001"],
                "claim": "直接覆盖认证模块内部成功和失败分支",
                "criterion_indices": [1, 2],
                "test_case_ids": ["test_login_branches"],
                "implementation_fingerprint": fingerprint,
            }
        )
        complete = self.workflow._test_strategy_audit()
        self.assertTrue(complete["passed"])
        self.assertEqual(complete["core_modules_white_box_covered"], 1)

    def test_explicit_trace_method_overrides_ambiguous_source_classification(self) -> None:
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_performance.py").write_text(
            '"""白盒静态检查与性能验证。"""\n\n'
            "import unittest\n\n"
            "class PerformanceTests(unittest.TestCase):\n"
            "    def test_response_time(self):\n"
            "        pass\n",
            encoding="utf-8",
        )
        evidence = Evidence(
            "performance-run", "run_command", True, "python -m unittest -v · exit 0", 1,
            verification=True,
        )
        self.workflow.record_evidence(
            evidence,
            {"command": "python -m unittest discover -s tests -v"},
            {
                "ok": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": (
                    "test_response_time (test_performance.PerformanceTests.test_response_time) ... ok\n"
                    "Ran 1 test in 0.010s\nOK"
                ),
            },
        )
        self.workflow._state["test_links"] = [
            {
                "requirement_id": "NFR-001",
                "command": "python -m unittest discover -s tests -v",
                "evidence_kind": "performance_test",
                "test_method": "black_box",
                "test_level": "performance",
                "claim": "验证响应时间",
                "criterion_indices": [1],
                "test_case_ids": ["test_performance.PerformanceTests.test_response_time"],
            }
        ]

        run = self.workflow._verification_summary()["latest_run"]
        self.assertEqual(len(run["cases"]), 1)
        self.assertEqual(run["black_box"]["total"], 1)
        self.assertEqual(run["white_box"]["total"], 0)
        self.assertEqual(run["unclassified"]["total"], 0)
        self.assertEqual(run["cases"][0]["level"], "performance")

    def test_verification_summary_preserves_failed_unittest_result(self) -> None:
        evidence = Evidence(
            "tests-failed", "run_command", False, "python -m unittest -v · exit 1", 1,
            verification=True,
        )
        self.workflow.record_evidence(
            evidence,
            {"command": "python -m unittest -v"},
            {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": (
                    "test_login (tests.test_auth.AuthTest.test_login) ... FAIL\n"
                    "Ran 1 test in 0.010s\nFAILED (failures=1)"
                ),
            },
        )

        run = self.workflow._verification_summary()["latest_run"]
        self.assertEqual(run["status"], "failed")
        self.assertEqual((run["total"], run["passed"], run["failed"]), (1, 0, 1))


if __name__ == "__main__":
    unittest.main()
