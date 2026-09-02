from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any, Mapping

from .planning import Evidence


PHASES = ("requirements", "design", "implementation", "verification", "acceptance")
PHASE_TITLES = {
    "requirements": "需求分析",
    "design": "结构化设计",
    "implementation": "结构化实现",
    "verification": "测试验证",
    "acceptance": "验收与变更控制",
}
APPROVAL_OPTION_IDS = {"approve", "approved", "accept", "accepted", "yes", "confirm", "confirmed"}
SKILLS = (
    {
        "id": "requirements-analysis",
        "phase": "requirements",
        "title": "需求分析 Skill",
        "description": "识别功能/非功能需求、验收标准、约束与待确认决策。",
    },
    {
        "id": "structured-design",
        "phase": "design",
        "title": "结构化设计 Skill",
        "description": "把已确认需求映射到模块、职责、接口与依赖。",
    },
    {
        "id": "structured-implementation",
        "phase": "implementation",
        "title": "结构化实现 Skill",
        "description": "依据设计实施小步变更，并绑定真实文件修改证据。",
    },
    {
        "id": "test-verification",
        "phase": "verification",
        "title": "测试验证 Skill",
        "description": "按需求建立测试追踪，并绑定成功的验证命令证据。",
    },
    {
        "id": "acceptance-change-control",
        "phase": "acceptance",
        "title": "验收与变更控制 Skill",
        "description": "汇总追踪矩阵、请求用户验收并记录后续变更。",
    },
)


class EngineeringError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        candidates: list[dict[str, Any]] | None = None,
        next_action: str = "",
    ):
        super().__init__(message)
        self.candidates = candidates or []
        self.next_action = next_action


class EngineeringWorkflow:
    """Persistent, evidence-gated software-engineering lifecycle for one workspace."""

    update_schema = {
        "type": "function",
        "function": {
            "name": "update_engineering_state",
            "description": (
                "Update Yukai-SE's project-level engineering lifecycle. Requirements need IDs and "
                "acceptance criteria; design modules map requirements; implementation and tests must "
                "cite evidence IDs from successful tools. Successful baseline approval, design definition, "
                "complete implementation coverage, and complete verification coverage advance automatically. "
                "Use advance_phase mainly to move backward for a change request."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "define_requirements",
                            "define_design",
                            "link_implementation",
                            "link_tests",
                            "advance_phase",
                            "complete_project",
                        ],
                    },
                    "project_title": {"type": "string", "maxLength": 120},
                    "requirements": {
                        "type": "array",
                        "maxItems": 40,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "pattern": "^(FR|NFR)-[0-9]{3}$",
                                    "description": "Functional IDs use FR-001; non-functional IDs use NFR-001.",
                                },
                                "title": {"type": "string"},
                                "kind": {"type": "string", "enum": ["functional", "non_functional"]},
                                "description": {"type": "string"},
                                "acceptance_criteria": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "string", "minLength": 1},
                                },
                            },
                            "required": ["id", "title", "kind", "description", "acceptance_criteria"],
                            "additionalProperties": False,
                        },
                    },
                    "assumptions": {
                        "type": "array",
                        "maxItems": 20,
                        "description": (
                            "Default decisions or assumptions that materially define the requirements baseline. "
                            "They will be shown to the user together with the full requirements before approval."
                        ),
                        "items": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "actors": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "pattern": "^ACT-[0-9]{3}$"},
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["id", "name", "description"],
                            "additionalProperties": False,
                        },
                    },
                    "use_cases": {
                        "type": "array",
                        "maxItems": 60,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "pattern": "^UC-[0-9]{3}$"},
                                "name": {"type": "string"},
                                "goal": {"type": "string"},
                                "actor_ids": {"type": "array", "items": {"type": "string"}},
                                "preconditions": {"type": "array", "items": {"type": "string"}},
                                "main_flow": {"type": "array", "items": {"type": "string"}},
                                "alternative_flows": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "condition": {"type": "string"},
                                            "steps": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["id", "condition", "steps"],
                                        "additionalProperties": False,
                                    },
                                },
                                "postconditions": {"type": "array", "items": {"type": "string"}},
                                "acceptance_links": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "requirement_id": {"type": "string", "pattern": "^FR-[0-9]{3}$"},
                                            "criterion_indices": {
                                                "type": "array",
                                                "items": {"type": "integer", "minimum": 1},
                                            },
                                        },
                                        "required": ["requirement_id", "criterion_indices"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": [
                                "id", "name", "goal", "actor_ids", "preconditions", "main_flow",
                                "alternative_flows", "postconditions", "acceptance_links"
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "use_case_relationships": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["include", "extend", "generalization"],
                                },
                                "label": {"type": "string"},
                            },
                            "required": ["from", "to", "type"],
                            "additionalProperties": False,
                        },
                    },
                    "modules": {
                        "type": "array",
                        "maxItems": 40,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "pattern": "^MOD-[0-9]{3}$",
                                    "description": "Module IDs use MOD-001, MOD-002, and so on.",
                                },
                                "name": {"type": "string"},
                                "responsibility": {"type": "string"},
                                "requirement_ids": {"type": "array", "items": {"type": "string"}},
                                "interfaces": {"type": "array", "items": {"type": "string"}},
                                "dependencies": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "name", "responsibility", "requirement_ids"],
                            "additionalProperties": False,
                        },
                    },
                    "uml_classes": {
                        "type": "array",
                        "maxItems": 60,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "pattern": "^CLS-[0-9]{3}$"},
                                "name": {"type": "string"},
                                "stereotype": {"type": "string"},
                                "attributes": {"type": "array", "items": {"type": "string"}},
                                "methods": {"type": "array", "items": {"type": "string"}},
                                "requirement_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "name", "attributes", "methods", "requirement_ids"],
                            "additionalProperties": False,
                        },
                    },
                    "uml_relationships": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["association", "inheritance", "composition", "aggregation", "dependency"],
                                },
                                "label": {"type": "string"},
                            },
                            "required": ["from", "to", "type"],
                            "additionalProperties": False,
                        },
                    },
                    "sequences": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "pattern": "^SEQ-[0-9]{3}$"},
                                "name": {"type": "string"},
                                "requirement_ids": {"type": "array", "items": {"type": "string"}},
                                "participants": {"type": "array", "items": {"type": "string"}},
                                "steps": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "from": {"type": "string"},
                                            "to": {"type": "string"},
                                            "message": {"type": "string"},
                                            "response": {"type": "boolean"},
                                        },
                                        "required": ["from", "to", "message"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["id", "name", "requirement_ids", "participants", "steps"],
                            "additionalProperties": False,
                        },
                    },
                    "process_flows": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "pattern": "^FLOW-[0-9]{3}$"},
                                "name": {"type": "string"},
                                "requirement_ids": {"type": "array", "items": {"type": "string"}},
                                "direction": {"type": "string", "enum": ["TD", "LR"]},
                                "nodes": {
                                    "type": "array",
                                    "maxItems": 60,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "type": {
                                                "type": "string",
                                                "enum": ["start", "process", "decision", "input_output", "end"],
                                            },
                                            "label": {"type": "string"},
                                        },
                                        "required": ["id", "type", "label"],
                                        "additionalProperties": False,
                                    },
                                },
                                "edges": {
                                    "type": "array",
                                    "maxItems": 100,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "from": {"type": "string"},
                                            "to": {"type": "string"},
                                            "label": {"type": "string"},
                                        },
                                        "required": ["from", "to"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["id", "name", "requirement_ids", "nodes", "edges"],
                            "additionalProperties": False,
                        },
                    },
                    "domain_objects": {
                        "type": "array",
                        "maxItems": 60,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "pattern": "^DOM-[0-9]{3}$"},
                                "name": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": ["aggregate_root", "entity", "value_object", "domain_service", "repository"],
                                },
                                "description": {"type": "string"},
                                "business_rules": {"type": "array", "items": {"type": "string"}},
                                "requirement_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "name", "kind", "description", "business_rules", "requirement_ids"],
                            "additionalProperties": False,
                        },
                    },
                    "links": {
                        "type": "array",
                        "maxItems": 80,
                        "description": (
                            "For link_tests, inspection evidence must come from read_file, search_text, "
                            "list_files, or get_environment. Test evidence must come from a successful "
                            "verification command; never label run_command evidence as inspection."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "requirement_id": {"type": "string", "pattern": "^(FR|NFR)-[0-9]{3}$"},
                                "module_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "For white_box test links, identify the internal design modules exercised. "
                                        "Each module must own the linked requirement."
                                    ),
                                },
                                "path": {"type": "string"},
                                "command": {"type": "string"},
                                "evidence_id": {"type": "string"},
                                "evidence_kind": {
                                    "type": "string",
                                    "enum": [
                                        "unit_test",
                                        "integration_test",
                                        "performance_test",
                                        "security_test",
                                        "supporting_test",
                                        "static_analysis",
                                        "inspection",
                                    ],
                                    "description": (
                                        "Required for link_tests. Functional requirements need unit_test or "
                                        "integration_test. Use supporting_test for unittest-based structural or "
                                        "dependency checks that are not business black-box/white-box tests."
                                    ),
                                },
                                "test_method": {
                                    "type": "string",
                                    "enum": ["black_box", "white_box"],
                                    "description": (
                                        "Required for unit, integration, performance, and security test evidence. "
                                        "Do not classify static analysis or inspection as a test method."
                                    ),
                                },
                                "test_level": {
                                    "type": "string",
                                    "enum": ["unit", "integration", "system", "acceptance", "performance", "security", "static"],
                                },
                                "claim": {
                                    "type": "string",
                                    "description": "Required for link_tests: what this evidence actually proves.",
                                },
                                "criterion_indices": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "integer", "minimum": 1},
                                    "description": "Required for link_tests: 1-based acceptance criteria covered by this evidence.",
                                },
                                "test_case_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 100,
                                    "description": (
                                        "Exact unittest function names or qualified IDs proved by this link. "
                                        "Dynamic black-box and white-box links must name at least one real case."
                                    ),
                                },
                            },
                            "required": ["requirement_id", "evidence_id"],
                            "additionalProperties": False,
                        },
                    },
                    "target_phase": {"type": "string", "enum": list(PHASES)},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    }

    question_schema = {
        "type": "function",
        "function": {
            "name": "request_user_input",
            "description": (
                "Pause the engineering workflow and ask the user for a material decision that cannot "
                "be discovered from the workspace. Do not ask about implementation details you can inspect."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "maxLength": 60},
                    "decision_key": {
                        "type": "string",
                        "description": "Use requirements_baseline, design_baseline, or project_acceptance for mandatory gates.",
                    },
                    "question": {"type": "string", "maxLength": 500},
                    "reason": {"type": "string", "maxLength": 500},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                                "requires_input": {
                                    "type": "boolean",
                                    "description": "Require the user to explain this choice in free text.",
                                },
                                "input_placeholder": {"type": "string", "maxLength": 160},
                            },
                            "required": ["id", "label"],
                            "additionalProperties": False,
                        },
                    },
                    "affected_requirement_ids": {
                        "type": "array",
                        "description": (
                            "Only IDs already present in the current engineering requirements. Use an empty "
                            "array for clarification questions asked before define_requirements."
                        ),
                        "items": {"type": "string"},
                    },
                },
                "required": ["question_id", "decision_key", "question", "reason", "options"],
                "additionalProperties": False,
            },
        },
    }

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.directory = self.workspace / ".yukai" / "engineering"
        self.path = self.directory / "project.json"
        self._lock = threading.RLock()
        self._state = self._load()

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [self.update_schema, self.question_schema]

    @property
    def is_completed(self) -> bool:
        with self._lock:
            return self._state["status"] == "completed"

    @property
    def phase(self) -> str:
        with self._lock:
            return str(self._state["phase"])

    def tool_snapshot(self) -> dict[str, Any]:
        """Small model-facing state; the UI can still request the full payload."""
        with self._lock:
            gate = self._gate(self._state["phase"])
            return {
                "phase": self._state["phase"],
                "status": self._state["status"],
                "project_title": self._state["project_title"],
                "counts": {
                    "requirements": len(self._state["requirements"]),
                    "actors": len(self._state.get("actors", [])),
                    "use_cases": len(self._state.get("use_cases", [])),
                    "modules": len(self._state["design_modules"]),
                    "implementation_links": len(self._state["implementation_links"]),
                    "test_links": len(self._state["test_links"]),
                    "evidence": len(self._state.get("evidence", [])),
                },
                "gate": gate,
                "pending_question_id": (
                    (self._state.get("pending_question") or {}).get("question_id")
                    if isinstance(self._state.get("pending_question"), dict)
                    else None
                ),
            }

    def evidence_records(self) -> dict[str, Evidence]:
        with self._lock:
            records: dict[str, Evidence] = {}
            for raw in self._state.get("evidence", []):
                if not isinstance(raw, dict) or not raw.get("id"):
                    continue
                records[str(raw["id"])] = Evidence(
                    evidence_id=str(raw["id"]),
                    tool=str(raw.get("tool", "")),
                    ok=bool(raw.get("ok")),
                    summary=str(raw.get("summary", "")),
                    step=int(raw.get("step", 0)),
                    verification=bool(raw.get("verification")),
                    error_type=str(raw.get("error_type", "")),
                    changed=bool(raw.get("changed", True)),
                )
            return records

    def record_evidence(
        self,
        evidence: Evidence,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        with self._lock:
            path = str(result.get("path") or arguments.get("path") or "")
            record = {
                **evidence.payload(),
                "timestamp": _now(),
                "path": path,
                "command": str(arguments.get("command", ""))[:1000],
                "exit_code": result.get("exit_code"),
                "file_hash": self._file_hash(path) if path and evidence.ok else "",
                "test_count": _test_count(result) if evidence.ok else None,
            }
            test_run = (
                _parse_unittest_run(str(arguments.get("command", "")), result)
                if evidence.verification
                else None
            )
            if test_run is not None:
                record["test_run"] = test_run
            existing = [
                item for item in self._state.get("evidence", []) if item.get("id") != evidence.evidence_id
            ]
            self._state["evidence"] = (existing + [record])[-500:]
            self._state["updated_at"] = _now()
            self._persist(write_documents=False)

    def completion_summary(self) -> str:
        with self._lock:
            requirements = self._state["requirements"]
            functional = sum(item["kind"] == "functional" for item in requirements)
            non_functional = len(requirements) - functional
            commands = list(
                dict.fromkeys(
                    item.get("command", "") for item in self._state["test_links"] if item.get("command")
                )
            )
            verification = "；".join(commands[:3]) or "已绑定的验证证据"
            latest_run = self._verification_summary().get("latest_run")
            strategy = self._test_strategy_audit()
            test_result = (
                f"{latest_run['passed']}/{latest_run['total']} 个真实测试用例通过"
                f"（黑盒 {strategy['black_box_cases']}，白盒 {strategy['white_box_cases']}）"
                if isinstance(latest_run, dict)
                else "未记录结构化测试用例"
            )
            return (
                "项目已完成并通过用户验收。\n\n"
                f"- 需求基线：{functional} 项功能需求，{non_functional} 项非功能需求\n"
                f"- 用例模型：{len(self._state.get('actors', []))} 个参与者，{len(self._state.get('use_cases', []))} 个用例\n"
                f"- 设计模块：{len(self._state['design_modules'])} 个\n"
                f"- 实现追踪：{len(self._state['implementation_links'])} 条\n"
                f"- 测试结果：{test_result}\n"
                f"- 验证追踪：{len(self._state['test_links'])} 条（{verification}）\n\n"
                "剩余风险：当前结论仅覆盖已确认需求及其验收标准；输入范围、运行环境或未声明约束发生变化时需要重新评估。"
            )

    def payload(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_invalidated_progress()
            self._refresh_completed_acceptance()
            value = deepcopy(self._state)
            value["evidence_count"] = len(value.get("evidence", []))
            value.pop("evidence", None)
            value["implementation_audit"] = self._implementation_audit()
            value["verification_summary"] = self._verification_summary()
            value["test_strategy_audit"] = self._test_strategy_audit()
            value["skills"] = list(SKILLS)
            value["phases"] = [
                {
                    "id": phase,
                    "title": PHASE_TITLES[phase],
                    "status": self._phase_status(phase),
                    "gate": self._gate(phase),
                }
                for phase in PHASES
            ]
            value["active_skill"] = next(
                (skill for skill in SKILLS if skill["phase"] == self._state["phase"]),
                SKILLS[-1],
            )
            return value

    def _refresh_invalidated_progress(self) -> None:
        if (
            PHASES.index(self._state["phase"]) > PHASES.index("implementation")
            and not self._gate("implementation")["passed"]
        ):
            self._state["phase"] = "implementation"
            self._state["status"] = "active"
            self._invalidate_decisions("project_acceptance")
            pending = self._state.get("pending_question")
            if isinstance(pending, dict) and pending.get("decision_key") == "project_acceptance":
                self._state["pending_question"] = None
            self._state["updated_at"] = _now()
            self._persist()
        elif (
            PHASES.index(self._state["phase"]) > PHASES.index("verification")
            and self._state["test_links"]
            and not self._gate("verification")["passed"]
        ):
            self._state["phase"] = "verification"
            self._state["status"] = "active"
            self._invalidate_decisions("project_acceptance")
            pending = self._state.get("pending_question")
            if isinstance(pending, dict) and pending.get("decision_key") == "project_acceptance":
                self._state["pending_question"] = None
            self._state["updated_at"] = _now()
            self._persist()

    def _refresh_completed_acceptance(self) -> None:
        """Finish an already-approved acceptance without another model tool call."""
        if self._state["phase"] != "acceptance" or self._state["status"] != "active":
            return
        change_decision = next(
            (
                item
                for item in reversed(self._state.get("decisions", []))
                if item.get("key") == "completed_project_change"
            ),
            None,
        )
        if change_decision and change_decision.get("option_id") in {
            "modify_current",
            "replace_current",
        }:
            return
        acceptance = next(
            (
                item
                for item in reversed(self._state.get("decisions", []))
                if item.get("key") == "project_acceptance"
            ),
            None,
        )
        if not _is_approved(acceptance) or not self._gate("acceptance")["passed"]:
            return
        self._state["status"] = "completed"
        self._state["updated_at"] = _now()
        self._persist()

    def system_context(self) -> str:
        state = self.payload()
        compact = {
            "phase": state["phase"],
            "status": state["status"],
            "project_title": state["project_title"],
            "requirements": state["requirements"],
            "assumptions": state["assumptions"],
            "actors": state["actors"],
            "use_cases": state["use_cases"],
            "use_case_relationships": state["use_case_relationships"],
            "design_modules": state["design_modules"],
            "implementation_audit": state["implementation_audit"],
            "implementation_links": state["implementation_links"],
            "test_links": state["test_links"],
            "decisions": state["decisions"],
            "pending_question": state["pending_question"],
            "quality_gates": {phase["id"]: phase["gate"] for phase in state["phases"]},
        }
        return (
            "\n\nYukai-SE software-engineering mode is enabled.\n"
            "This lifecycle is the only user-visible plan in engineering mode; do not call update_plan. "
            "Follow requirements -> design -> implementation -> verification -> acceptance. "
            "Use update_engineering_state to maintain traceability. Before moving to design, ask the user "
            "to approve the requirements baseline with decision_key=requirements_baseline. Define actors, complete "
            "use-case specifications, and include/extend relationships together with requirements; every functional "
            "acceptance criterion must be covered by a use case. Define design_modules, "
            "uml_classes, uml_relationships, sequences, process_flows, and domain_objects in define_design, then request mandatory "
            "design approval with decision_key=design_baseline before implementation. Before completing, "
            "ask for project acceptance with decision_key=project_acceptance. Ask only about choices that "
            "materially change scope, architecture, constraints, or acceptance; inspect discoverable facts yourself. "
            "A user question must be the last action of the turn. Phases advance automatically after their "
            "gate passes, so inspect the returned engineering.phase before attempting advance_phase. For every "
            "dynamic test verification link, state black_box or white_box test_method, test_level, evidence_kind, "
            "the exact claim, which 1-based acceptance criteria it proves, and test_case_ids using exact test "
            "function names from the successful run. Run Python unittest with -v so each executed case is available "
            "for evidence validation. Label unittest test modules or classes clearly as black-box "
            "or white-box so individual cases can be classified. Black-box tests must prove every functional "
            "acceptance criterion through public behavior. White-box tests must directly exercise the internal "
            "branches, state transitions, algorithms, or data invariants of every design module that owns a "
            "functional requirement, and each white-box link must name those module_ids. Import scans, dependency "
            "checks, lint, and source inspection are supporting evidence, not business white-box tests. Use "
            "supporting_test with test_level=static and exact test_case_ids for unittest-based compliance checks; "
            "omit test_method and module_ids for them. Static "
            "analysis and inspection are "
            "supporting verification evidence, not black-box or white-box tests. Inspection evidence only comes "
            "from read_file, search_text, list_files, or "
            "get_environment; never label run_command as inspection. Record every material default in the "
            "assumptions field of define_requirements so it appears on the baseline review card. The engineering "
            "workspace may be changed only during implementation. In verification, only recognized test, lint, "
            "type-check, compile, dependency, or build commands are allowed. If project files or user documents "
            "need changes, move back to implementation and refresh their evidence before testing again. When a "
            "link_implementation action must cite the successful write_file, edit_file, or make_directory evidence "
            "for the exact changed path; never substitute read_file evidence. Each module_id may be used only when "
            "that module owns the linked requirement in the approved design baseline. Treat the returned "
            "implementation gate missing list as a checklist, and do not call link_tests until the returned "
            "engineering.phase is verification. When a "
            "completed workspace receives a new project or change request, first ask for decision_key="
            "completed_project_change with modify_current, replace_current, and new_workspace options. "
            "Before define_requirements has created requirement IDs, request_user_input must use an empty "
            "affected_requirement_ids array; never reference planned FR/NFR IDs as if they already exist. "
            "lifecycle remains authoritative even if older conversation history mentions update_plan. Never claim "
            "zero residual risk; describe the boundary of the verified baseline.\n"
            "Current engineering state:\n"
            + json.dumps(compact, ensure_ascii=False, default=str)
        )

    def update(
        self, arguments: dict[str, Any], evidence: Mapping[str, Evidence]
    ) -> dict[str, Any]:
        with self._lock:
            try:
                combined_evidence = self.evidence_records()
                combined_evidence.update(evidence)
                action = str(arguments.get("action", ""))
                if action == "define_requirements":
                    self._define_requirements(arguments)
                elif action == "define_design":
                    self._define_design(arguments)
                elif action == "link_implementation":
                    self._link_implementation(arguments, combined_evidence)
                elif action == "link_tests":
                    self._link_tests(arguments, combined_evidence)
                elif action == "advance_phase":
                    self._advance_phase(arguments)
                elif action == "complete_project":
                    self._complete_project()
                else:
                    raise EngineeringError(f"unknown engineering action: {action}")
                self._state["updated_at"] = _now()
                self._persist()
                return {"ok": True, "engineering": self.tool_snapshot()}
            except EngineeringError as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "error_type": "engineering_gate_failed",
                    "next_action": exc.next_action or _next_action(str(exc), self._state["phase"]),
                    "actual_evidence": _actual_evidence_from_error(str(exc)),
                    "candidate_evidence": exc.candidates,
                    "engineering": self.tool_snapshot(),
                }

    def request_user_input(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            try:
                question_id = _identifier(arguments.get("question_id"), "question_id")
                decision_key = _identifier(arguments.get("decision_key"), "decision_key")
                question = _text(arguments.get("question"), "question", 500)
                reason = _text(arguments.get("reason"), "reason", 500)
                raw_options = arguments.get("options")
                if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 4:
                    raise EngineeringError("options must contain between 2 and 4 choices")
                options = []
                for raw in raw_options:
                    if not isinstance(raw, dict):
                        raise EngineeringError("each option must be an object")
                    options.append(
                        {
                            "id": _identifier(raw.get("id"), "option id"),
                            "label": _text(raw.get("label"), "option label", 100),
                            "description": str(raw.get("description", "")).strip()[:300],
                            "requires_input": bool(raw.get("requires_input", False)),
                            "input_placeholder": str(raw.get("input_placeholder", "")).strip()[:160],
                        }
                    )
                for option in options:
                    option_id = option["id"].lower()
                    if option_id in {"revise", "modify", "change", "needs_changes", "other"}:
                        option["requires_input"] = True
                        option["input_placeholder"] = option["input_placeholder"] or "请说明需要修改的内容"
                if len({item["id"] for item in options}) != len(options):
                    raise EngineeringError("option IDs must be unique")
                if decision_key in {"requirements_baseline", "design_baseline", "project_acceptance"} and not any(
                    item["id"].lower() in APPROVAL_OPTION_IDS for item in options
                ):
                    raise EngineeringError(
                        f"{decision_key} options must include an approval ID such as approve"
                    )
                if decision_key == "requirements_baseline":
                    missing = self._requirements_content_missing()
                    if missing:
                        raise EngineeringError(
                            "complete requirements and use-case model before requesting baseline approval: "
                            + "; ".join(missing)
                        )
                if decision_key == "design_baseline":
                    if self._state["phase"] != "design":
                        raise EngineeringError("design baseline approval is only valid in the design phase")
                    missing = self._design_content_missing()
                    if missing:
                        raise EngineeringError(
                            "complete structured design before requesting design approval: " + "; ".join(missing)
                        )
                if decision_key == "project_acceptance" and not self._gate("verification")["passed"]:
                    raise EngineeringError(
                        "verification quality gate must pass before requesting project acceptance"
                    )
                if decision_key == "completed_project_change":
                    if not self.is_completed:
                        raise EngineeringError(
                            "completed_project_change is only valid for an accepted project"
                        )
                    required = {"modify_current", "replace_current", "new_workspace"}
                    actual = {item["id"] for item in options}
                    if not required.issubset(actual):
                        raise EngineeringError(
                            "completed_project_change options must include modify_current, replace_current, and new_workspace"
                        )
                affected = arguments.get("affected_requirement_ids", [])
                if not isinstance(affected, list):
                    raise EngineeringError("affected_requirement_ids must be an array")
                known = {item["id"] for item in self._state["requirements"]}
                unknown = {str(item) for item in affected} - known
                if unknown:
                    raise EngineeringError(f"unknown affected requirement IDs: {', '.join(sorted(unknown))}")
                question_value = {
                    "question_id": question_id,
                    "decision_key": decision_key,
                    "question": question,
                    "reason": reason,
                    "options": options,
                    "affected_requirement_ids": [str(item) for item in affected],
                    "asked_at": _now(),
                }
                if decision_key == "requirements_baseline":
                    question_value["baseline_review"] = {
                        "requirements": deepcopy(self._state["requirements"]),
                        "assumptions": list(self._state["assumptions"]),
                        "actors": deepcopy(self._state["actors"]),
                        "use_cases": deepcopy(self._state["use_cases"]),
                        "use_case_relationships": deepcopy(self._state["use_case_relationships"]),
                        "digest": self._requirements_digest(),
                    }
                if decision_key == "design_baseline":
                    question_value["design_review"] = {
                        "modules": deepcopy(self._state["design_modules"]),
                        "uml_classes": deepcopy(self._state["uml_classes"]),
                        "uml_relationships": deepcopy(self._state["uml_relationships"]),
                        "sequences": deepcopy(self._state["sequences"]),
                        "process_flows": deepcopy(self._state["process_flows"]),
                        "domain_objects": deepcopy(self._state["domain_objects"]),
                        "digest": self._design_digest(),
                    }
                if decision_key == "project_acceptance":
                    question_value["review_summary"] = self._review_summary()
                if decision_key == "completed_project_change":
                    question_value["workspace_review"] = {
                        "project_title": self._state["project_title"] or "未命名项目",
                        "requirements": len(self._state["requirements"]),
                        "workspace": str(self.workspace),
                        "warning": "替换当前项目可能删除或覆盖现有项目文件；建议为无关项目选择新的工作区。",
                    }
                self._state["pending_question"] = question_value
                self._state["status"] = "awaiting_user"
                self._state["updated_at"] = _now()
                self._persist()
                return {"ok": True, "awaiting_user": True, "question": question_value}
            except EngineeringError as exc:
                return {"ok": False, "error": str(exc), "error_type": "invalid_user_question"}

    def answer_question(
        self, question_id: str, *, option_id: str, answer: str = ""
    ) -> dict[str, Any]:
        with self._lock:
            pending = self._state.get("pending_question")
            if not isinstance(pending, dict) or pending.get("question_id") != question_id:
                raise EngineeringError("the engineering question is no longer pending")
            option = next(
                (item for item in pending["options"] if item["id"] == option_id), None
            )
            if option is None:
                raise EngineeringError("the selected option does not belong to this question")
            normalized_answer = answer.strip()[:1000]
            if option.get("requires_input") and (
                not normalized_answer or normalized_answer == option.get("label")
            ):
                raise EngineeringError("this choice requires a free-text explanation")
            if (
                pending.get("decision_key") == "requirements_baseline"
                and option_id.lower() in APPROVAL_OPTION_IDS
                and pending.get("baseline_review", {}).get("digest") != self._requirements_digest()
            ):
                raise EngineeringError(
                    "the requirements changed after this confirmation card was created; review the refreshed baseline"
                )
            if (
                pending.get("decision_key") == "design_baseline"
                and option_id.lower() in APPROVAL_OPTION_IDS
                and pending.get("design_review", {}).get("digest") != self._design_digest()
            ):
                raise EngineeringError(
                    "the design changed after this confirmation card was created; review the refreshed design baseline"
                )
            if (
                pending.get("decision_key") == "project_acceptance"
                and option_id.lower() in APPROVAL_OPTION_IDS
                and (
                    self._state["phase"] != "acceptance"
                    or not self._gate("verification")["passed"]
                )
            ):
                raise EngineeringError(
                    "verification changed after this acceptance card was created; review the refreshed result"
                )
            decision = {
                "key": pending["decision_key"],
                "question_id": question_id,
                "option_id": option_id,
                "option_label": option["label"],
                "answer": normalized_answer,
                "baseline_digest": pending.get("baseline_review", {}).get("digest", ""),
                "design_digest": pending.get("design_review", {}).get("digest", ""),
                "decided_at": _now(),
            }
            self._state["decisions"] = [
                item for item in self._state["decisions"] if item.get("key") != decision["key"]
            ] + [decision]
            self._state["pending_question"] = None
            self._state["status"] = "active"
            if (
                decision["key"] == "completed_project_change"
                and decision["option_id"] == "new_workspace"
            ):
                self._state["status"] = "completed"
            if decision["key"] == "requirements_baseline" and _is_approved(decision):
                gate = self._gate("requirements")
                if gate["passed"]:
                    self._state["phase"] = "design"
            if decision["key"] == "design_baseline" and _is_approved(decision):
                gate = self._gate("design")
                if gate["passed"]:
                    self._state["phase"] = "implementation"
            if decision["key"] == "project_acceptance" and _is_approved(decision):
                self._complete_project()
            self._state["updated_at"] = _now()
            self._persist()
            return {"ok": True, "decision": decision, "engineering": self.payload()}

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._state = _default_state()
            self._persist()
            return self.payload()

    def _define_requirements(self, arguments: dict[str, Any]) -> None:
        if self._state["phase"] != "requirements":
            raise EngineeringError("move back to the requirements phase before changing requirements")
        raw_items = arguments.get("requirements")
        if not isinstance(raw_items, list) or not raw_items:
            raise EngineeringError("define_requirements requires at least one requirement")
        requirements = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise EngineeringError("each requirement must be an object")
            item_id = _identifier(raw.get("id"), "requirement id").upper()
            kind = str(raw.get("kind", ""))
            expected_prefix = "FR-" if kind == "functional" else "NFR-"
            if kind not in {"functional", "non_functional"} or not re.fullmatch(
                rf"{expected_prefix}\d{{3}}", item_id
            ):
                raise EngineeringError("requirement IDs must use FR-001 or NFR-001 format matching kind")
            criteria = raw.get("acceptance_criteria")
            if not isinstance(criteria, list) or not criteria:
                raise EngineeringError(f"{item_id} requires at least one acceptance criterion")
            requirements.append(
                {
                    "id": item_id,
                    "title": _text(raw.get("title"), f"title for {item_id}", 120),
                    "kind": kind,
                    "description": _text(raw.get("description"), f"description for {item_id}", 1000),
                    "acceptance_criteria": [_text(value, f"criterion for {item_id}", 500) for value in criteria],
                }
            )
        if len({item["id"] for item in requirements}) != len(requirements):
            raise EngineeringError("requirement IDs must be unique")
        raw_assumptions = arguments.get("assumptions", [])
        if not isinstance(raw_assumptions, list):
            raise EngineeringError("assumptions must be an array")
        assumptions = [
            _text(value, "requirement assumption", 500) for value in raw_assumptions
        ]
        actors, use_cases, use_case_relationships = self._parse_use_case_model(
            arguments.get("actors"),
            arguments.get("use_cases"),
            arguments.get("use_case_relationships", []),
            requirements,
        )
        self._state["project_title"] = str(arguments.get("project_title") or self._state["project_title"]).strip()[:120]
        self._state["assumptions"] = assumptions
        self._state["requirements"] = requirements
        self._state["actors"] = actors
        self._state["use_cases"] = use_cases
        self._state["use_case_relationships"] = use_case_relationships
        self._state["use_case_model_required"] = True
        self._state["design_modules"] = []
        self._state["uml_classes"] = []
        self._state["uml_relationships"] = []
        self._state["sequences"] = []
        self._state["process_flows"] = []
        self._state["domain_objects"] = []
        self._state["implementation_links"] = []
        self._state["test_links"] = []
        self._invalidate_decisions("requirements_baseline", "design_baseline", "project_acceptance")
        pending = self._state.get("pending_question")
        if isinstance(pending, dict) and pending.get("decision_key") == "requirements_baseline":
            self._state["pending_question"] = None
        self._state["status"] = "active"

    def _parse_use_case_model(
        self,
        raw_actors: Any,
        raw_use_cases: Any,
        raw_relationships: Any,
        requirements: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        if not isinstance(raw_actors, list) or not raw_actors:
            raise EngineeringError("define_requirements requires at least one use-case actor")
        actors: list[dict[str, Any]] = []
        for raw in raw_actors:
            if not isinstance(raw, dict):
                raise EngineeringError("each use-case actor must be an object")
            actor_id = _identifier(raw.get("id"), "actor id").upper()
            if not re.fullmatch(r"ACT-\d{3}", actor_id):
                raise EngineeringError("actor IDs must use ACT-001 format")
            actors.append(
                {
                    "id": actor_id,
                    "name": _text(raw.get("name"), f"name for {actor_id}", 100),
                    "description": _text(
                        raw.get("description"), f"description for {actor_id}", 400
                    ),
                }
            )
        actor_ids = {item["id"] for item in actors}
        if len(actor_ids) != len(actors):
            raise EngineeringError("actor IDs must be unique")
        if not isinstance(raw_use_cases, list) or not raw_use_cases:
            raise EngineeringError("define_requirements requires at least one use case")
        requirement_by_id = {item["id"]: item for item in requirements}
        functional_ids = {
            item["id"] for item in requirements if item["kind"] == "functional"
        }
        use_cases: list[dict[str, Any]] = []
        for raw in raw_use_cases:
            if not isinstance(raw, dict):
                raise EngineeringError("each use case must be an object")
            use_case_id = _identifier(raw.get("id"), "use case id").upper()
            if not re.fullmatch(r"UC-\d{3}", use_case_id):
                raise EngineeringError("use case IDs must use UC-001 format")
            raw_actor_ids = raw.get("actor_ids")
            if not isinstance(raw_actor_ids, list) or not raw_actor_ids:
                raise EngineeringError(f"{use_case_id} requires at least one actor")
            linked_actor_ids = list(
                dict.fromkeys(str(value).upper() for value in raw_actor_ids)
            )
            unknown_actors = set(linked_actor_ids) - actor_ids
            if unknown_actors:
                raise EngineeringError(
                    f"{use_case_id} references unknown actors: {', '.join(sorted(unknown_actors))}"
                )
            preconditions = _text_array(
                raw.get("preconditions"), f"preconditions for {use_case_id}", allow_empty=True
            )
            main_flow = _text_array(raw.get("main_flow"), f"main flow for {use_case_id}")
            postconditions = _text_array(
                raw.get("postconditions"), f"postconditions for {use_case_id}"
            )
            raw_alternatives = raw.get("alternative_flows")
            if not isinstance(raw_alternatives, list):
                raise EngineeringError(f"alternative_flows for {use_case_id} must be an array")
            alternative_flows: list[dict[str, Any]] = []
            for raw_alternative in raw_alternatives:
                if not isinstance(raw_alternative, dict):
                    raise EngineeringError(
                        f"each alternative flow for {use_case_id} must be an object"
                    )
                alternative_id = _identifier(
                    raw_alternative.get("id"), f"alternative flow id for {use_case_id}"
                ).upper()
                alternative_flows.append(
                    {
                        "id": alternative_id,
                        "condition": _text(
                            raw_alternative.get("condition"),
                            f"condition for {use_case_id}/{alternative_id}",
                            300,
                        ),
                        "steps": _text_array(
                            raw_alternative.get("steps"),
                            f"steps for {use_case_id}/{alternative_id}",
                        ),
                    }
                )
            if len({item["id"] for item in alternative_flows}) != len(alternative_flows):
                raise EngineeringError(f"alternative flow IDs for {use_case_id} must be unique")
            raw_links = raw.get("acceptance_links")
            if not isinstance(raw_links, list) or not raw_links:
                raise EngineeringError(f"{use_case_id} requires acceptance_links")
            acceptance_links: list[dict[str, Any]] = []
            for raw_link in raw_links:
                if not isinstance(raw_link, dict):
                    raise EngineeringError(
                        f"each acceptance link for {use_case_id} must be an object"
                    )
                requirement_id = str(raw_link.get("requirement_id", "")).upper()
                if requirement_id not in functional_ids:
                    raise EngineeringError(
                        f"{use_case_id} may only reference known functional requirements: {requirement_id}"
                    )
                raw_indices = raw_link.get("criterion_indices")
                if not isinstance(raw_indices, list) or not raw_indices or not all(
                    isinstance(value, int) and not isinstance(value, bool) for value in raw_indices
                ):
                    raise EngineeringError(
                        f"criterion_indices for {use_case_id}/{requirement_id} must be non-empty integers"
                    )
                indices = sorted(set(raw_indices))
                criterion_count = len(requirement_by_id[requirement_id]["acceptance_criteria"])
                if indices[0] < 1 or indices[-1] > criterion_count:
                    raise EngineeringError(
                        f"criterion_indices for {use_case_id}/{requirement_id} must be between 1 and {criterion_count}"
                    )
                acceptance_links.append(
                    {"requirement_id": requirement_id, "criterion_indices": indices}
                )
            if len({item["requirement_id"] for item in acceptance_links}) != len(acceptance_links):
                raise EngineeringError(
                    f"acceptance_links for {use_case_id} must not repeat a requirement"
                )
            use_cases.append(
                {
                    "id": use_case_id,
                    "name": _text(raw.get("name"), f"name for {use_case_id}", 120),
                    "goal": _text(raw.get("goal"), f"goal for {use_case_id}", 500),
                    "actor_ids": linked_actor_ids,
                    "preconditions": preconditions,
                    "main_flow": main_flow,
                    "alternative_flows": alternative_flows,
                    "postconditions": postconditions,
                    "acceptance_links": acceptance_links,
                    "requirement_ids": [item["requirement_id"] for item in acceptance_links],
                }
            )
        use_case_ids = {item["id"] for item in use_cases}
        if len(use_case_ids) != len(use_cases):
            raise EngineeringError("use case IDs must be unique")
        unassociated_actors = actor_ids - {
            actor_id for item in use_cases for actor_id in item["actor_ids"]
        }
        if unassociated_actors:
            raise EngineeringError(
                "actors must participate in at least one use case: "
                + ", ".join(sorted(unassociated_actors))
            )
        if not isinstance(raw_relationships, list):
            raise EngineeringError("use_case_relationships must be an array")
        relationships: list[dict[str, str]] = []
        allowed_relationships = {"include", "extend", "generalization"}
        for raw in raw_relationships:
            if not isinstance(raw, dict):
                raise EngineeringError("each use-case relationship must be an object")
            source = str(raw.get("from", "")).upper()
            target = str(raw.get("to", "")).upper()
            relation_type = str(raw.get("type", ""))
            if source not in use_case_ids or target not in use_case_ids:
                raise EngineeringError(
                    f"use-case relationship references unknown use cases: {source} -> {target}"
                )
            if source == target:
                raise EngineeringError("a use case cannot relate to itself")
            if relation_type not in allowed_relationships:
                raise EngineeringError(f"unsupported use-case relationship type: {relation_type}")
            relationships.append(
                {
                    "from": source,
                    "to": target,
                    "type": relation_type,
                    "label": str(raw.get("label", "")).strip()[:120],
                }
            )
        return actors, use_cases, relationships

    def _define_design(self, arguments: dict[str, Any]) -> None:
        if self._state["phase"] != "design":
            raise EngineeringError("move to the design phase before defining design modules")
        gate = self._gate("requirements")
        if not gate["passed"]:
            raise EngineeringError("requirements quality gate failed: " + "; ".join(gate["missing"]))
        raw_items = arguments.get("modules")
        if not isinstance(raw_items, list) or not raw_items:
            raise EngineeringError("define_design requires at least one module")
        known = {item["id"] for item in self._state["requirements"]}
        modules = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise EngineeringError("each design module must be an object")
            item_id = _identifier(raw.get("id"), "module id").upper()
            if not re.fullmatch(r"MOD-\d{3}", item_id):
                raise EngineeringError("module IDs must use MOD-001 format")
            requirement_ids = raw.get("requirement_ids")
            if not isinstance(requirement_ids, list) or not requirement_ids:
                raise EngineeringError(f"{item_id} must map at least one requirement")
            mapped = [str(value).upper() for value in requirement_ids]
            unknown = set(mapped) - known
            if unknown:
                raise EngineeringError(f"{item_id} maps unknown requirements: {', '.join(sorted(unknown))}")
            interfaces = raw.get("interfaces", [])
            if not isinstance(interfaces, list):
                raise EngineeringError(f"interfaces for {item_id} must be an array")
            dependencies = raw.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise EngineeringError(f"dependencies for {item_id} must be an array")
            modules.append(
                {
                    "id": item_id,
                    "name": _text(raw.get("name"), f"name for {item_id}", 120),
                    "responsibility": _text(raw.get("responsibility"), f"responsibility for {item_id}", 800),
                    "requirement_ids": list(dict.fromkeys(mapped)),
                    "interfaces": [str(value).strip()[:300] for value in interfaces if str(value).strip()],
                    "dependencies": [str(value).upper() for value in dependencies],
                }
            )
        if len({item["id"] for item in modules}) != len(modules):
            raise EngineeringError("module IDs must be unique")
        module_ids = {item["id"] for item in modules}
        for module in modules:
            unknown_dependencies = set(module["dependencies"]) - module_ids
            if unknown_dependencies:
                raise EngineeringError(
                    f"{module['id']} depends on unknown modules: {', '.join(sorted(unknown_dependencies))}"
                )
            if module["id"] in module["dependencies"]:
                raise EngineeringError(f"{module['id']} cannot depend on itself")

        uml_classes = self._parse_uml_classes(arguments.get("uml_classes"), known)
        uml_relationships = self._parse_uml_relationships(
            arguments.get("uml_relationships", []), {item["id"] for item in uml_classes}
        )
        sequences = self._parse_sequences(arguments.get("sequences"), known)
        process_flows = self._parse_process_flows(arguments.get("process_flows"), known)
        domain_objects = self._parse_domain_objects(arguments.get("domain_objects"), known)
        self._state["design_modules"] = modules
        self._state["uml_classes"] = uml_classes
        self._state["uml_relationships"] = uml_relationships
        self._state["sequences"] = sequences
        self._state["process_flows"] = process_flows
        self._state["process_flow_required"] = True
        self._state["domain_objects"] = domain_objects
        self._state["implementation_links"] = []
        self._state["test_links"] = []
        self._invalidate_decisions("design_baseline", "project_acceptance")

    def _parse_uml_classes(self, raw_items: Any, known_requirements: set[str]) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or not raw_items:
            raise EngineeringError("define_design requires at least one UML class")
        result = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise EngineeringError("each UML class must be an object")
            item_id = _identifier(raw.get("id"), "UML class id").upper()
            if not re.fullmatch(r"CLS-\d{3}", item_id):
                raise EngineeringError("UML class IDs must use CLS-001 format")
            requirement_ids = _requirement_references(raw.get("requirement_ids"), known_requirements, item_id)
            attributes = raw.get("attributes", [])
            methods = raw.get("methods", [])
            if not isinstance(attributes, list) or not isinstance(methods, list):
                raise EngineeringError(f"attributes and methods for {item_id} must be arrays")
            result.append({
                "id": item_id,
                "name": _text(raw.get("name"), f"name for {item_id}", 120),
                "stereotype": str(raw.get("stereotype", "class")).strip()[:80] or "class",
                "attributes": [str(value).strip()[:200] for value in attributes if str(value).strip()],
                "methods": [str(value).strip()[:200] for value in methods if str(value).strip()],
                "requirement_ids": requirement_ids,
            })
        if len({item["id"] for item in result}) != len(result):
            raise EngineeringError("UML class IDs must be unique")
        return result

    def _parse_uml_relationships(self, raw_items: Any, known_classes: set[str]) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            raise EngineeringError("uml_relationships must be an array")
        result = []
        allowed = {"association", "inheritance", "composition", "aggregation", "dependency"}
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise EngineeringError("each UML relationship must be an object")
            source = str(raw.get("from", "")).upper()
            target = str(raw.get("to", "")).upper()
            if source not in known_classes or target not in known_classes:
                raise EngineeringError(f"UML relationship references unknown classes: {source} -> {target}")
            relation_type = str(raw.get("type", ""))
            if relation_type not in allowed:
                raise EngineeringError(f"unsupported UML relationship type: {relation_type}")
            result.append({"from": source, "to": target, "type": relation_type, "label": str(raw.get("label", "")).strip()[:160]})
        return result

    def _parse_sequences(self, raw_items: Any, known_requirements: set[str]) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or not raw_items:
            raise EngineeringError("define_design requires at least one sequence diagram")
        result = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise EngineeringError("each sequence must be an object")
            item_id = _identifier(raw.get("id"), "sequence id").upper()
            if not re.fullmatch(r"SEQ-\d{3}", item_id):
                raise EngineeringError("sequence IDs must use SEQ-001 format")
            participants = raw.get("participants")
            steps = raw.get("steps")
            if not isinstance(participants, list) or len(participants) < 2:
                raise EngineeringError(f"{item_id} requires at least two participants")
            participant_names = [str(value).strip()[:100] for value in participants if str(value).strip()]
            if len(set(participant_names)) != len(participant_names):
                raise EngineeringError(f"participants for {item_id} must be unique")
            if not isinstance(steps, list) or not steps:
                raise EngineeringError(f"{item_id} requires at least one interaction step")
            parsed_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    raise EngineeringError(f"each step for {item_id} must be an object")
                source = str(step.get("from", "")).strip()
                target = str(step.get("to", "")).strip()
                if source not in participant_names or target not in participant_names:
                    raise EngineeringError(f"{item_id} step references an unknown participant: {source} -> {target}")
                parsed_steps.append({
                    "from": source,
                    "to": target,
                    "message": _text(step.get("message"), f"message for {item_id}", 300),
                    "response": bool(step.get("response", False)),
                })
            result.append({
                "id": item_id,
                "name": _text(raw.get("name"), f"name for {item_id}", 120),
                "requirement_ids": _requirement_references(raw.get("requirement_ids"), known_requirements, item_id),
                "participants": participant_names,
                "steps": parsed_steps,
            })
        if len({item["id"] for item in result}) != len(result):
            raise EngineeringError("sequence IDs must be unique")
        return result

    def _parse_process_flows(
        self, raw_items: Any, known_requirements: set[str]
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or not raw_items:
            raise EngineeringError("define_design requires at least one system business process flow")
        result: list[dict[str, Any]] = []
        allowed_types = {"start", "process", "decision", "input_output", "end"}
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise EngineeringError("each process flow must be an object")
            item_id = _identifier(raw.get("id"), "process flow id").upper()
            if not re.fullmatch(r"FLOW-\d{3}", item_id):
                raise EngineeringError("process flow IDs must use FLOW-001 format")
            raw_nodes = raw.get("nodes")
            raw_edges = raw.get("edges")
            if not isinstance(raw_nodes, list) or len(raw_nodes) < 2:
                raise EngineeringError(f"{item_id} requires at least two nodes")
            if not isinstance(raw_edges, list) or not raw_edges:
                raise EngineeringError(f"{item_id} requires at least one edge")
            nodes: list[dict[str, str]] = []
            for raw_node in raw_nodes:
                if not isinstance(raw_node, dict):
                    raise EngineeringError(f"each node for {item_id} must be an object")
                node_id = _identifier(raw_node.get("id"), f"node id for {item_id}").upper()
                node_type = str(raw_node.get("type", ""))
                if node_type not in allowed_types:
                    raise EngineeringError(f"unsupported node type for {item_id}/{node_id}: {node_type}")
                nodes.append(
                    {
                        "id": node_id,
                        "type": node_type,
                        "label": _text(raw_node.get("label"), f"label for {item_id}/{node_id}", 200),
                    }
                )
            node_ids = {item["id"] for item in nodes}
            if len(node_ids) != len(nodes):
                raise EngineeringError(f"node IDs for {item_id} must be unique")
            start_nodes = [item["id"] for item in nodes if item["type"] == "start"]
            end_nodes = [item["id"] for item in nodes if item["type"] == "end"]
            if len(start_nodes) != 1:
                raise EngineeringError(f"{item_id} requires exactly one start node")
            if not end_nodes:
                raise EngineeringError(f"{item_id} requires at least one end node")
            edges: list[dict[str, str]] = []
            for raw_edge in raw_edges:
                if not isinstance(raw_edge, dict):
                    raise EngineeringError(f"each edge for {item_id} must be an object")
                source = str(raw_edge.get("from", "")).upper()
                target = str(raw_edge.get("to", "")).upper()
                if source not in node_ids or target not in node_ids:
                    raise EngineeringError(
                        f"{item_id} edge references an unknown node: {source} -> {target}"
                    )
                if source == target:
                    raise EngineeringError(f"{item_id} edge cannot point a node to itself: {source}")
                edges.append(
                    {
                        "from": source,
                        "to": target,
                        "label": str(raw_edge.get("label", "")).strip()[:120],
                    }
                )
            if any(edge["to"] in start_nodes for edge in edges):
                raise EngineeringError(f"{item_id} start node cannot have incoming edges")
            if any(edge["from"] in end_nodes for edge in edges):
                raise EngineeringError(f"{item_id} end nodes cannot have outgoing edges")
            outgoing: dict[str, list[dict[str, str]]] = {node_id: [] for node_id in node_ids}
            for edge in edges:
                outgoing[edge["from"]].append(edge)
            for node in nodes:
                branches = outgoing[node["id"]]
                if node["type"] == "decision" and (
                    len(branches) < 2 or any(not edge["label"] for edge in branches)
                ):
                    raise EngineeringError(
                        f"decision node {item_id}/{node['id']} requires at least two labeled branches"
                    )
                if node["type"] not in {"decision", "end"} and not branches:
                    raise EngineeringError(f"{item_id}/{node['id']} must connect to a following node")
            reachable = {start_nodes[0]}
            pending = [start_nodes[0]]
            while pending:
                current = pending.pop()
                for edge in outgoing[current]:
                    if edge["to"] not in reachable:
                        reachable.add(edge["to"])
                        pending.append(edge["to"])
            unreachable = node_ids - reachable
            if unreachable:
                raise EngineeringError(
                    f"{item_id} contains nodes unreachable from start: {', '.join(sorted(unreachable))}"
                )
            incoming: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
            for edge in edges:
                incoming[edge["to"]].append(edge["from"])
            terminating = set(end_nodes)
            pending = list(end_nodes)
            while pending:
                current = pending.pop()
                for source in incoming[current]:
                    if source not in terminating:
                        terminating.add(source)
                        pending.append(source)
            non_terminating = node_ids - terminating
            if non_terminating:
                raise EngineeringError(
                    f"{item_id} contains nodes with no path to an end: "
                    + ", ".join(sorted(non_terminating))
                )
            direction = str(raw.get("direction", "TD")).upper()
            if direction not in {"TD", "LR"}:
                raise EngineeringError(f"direction for {item_id} must be TD or LR")
            result.append(
                {
                    "id": item_id,
                    "name": _text(raw.get("name"), f"name for {item_id}", 120),
                    "requirement_ids": _requirement_references(
                        raw.get("requirement_ids"), known_requirements, item_id
                    ),
                    "direction": direction,
                    "nodes": nodes,
                    "edges": edges,
                }
            )
        if len({item["id"] for item in result}) != len(result):
            raise EngineeringError("process flow IDs must be unique")
        return result

    def _parse_domain_objects(self, raw_items: Any, known_requirements: set[str]) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or not raw_items:
            raise EngineeringError("define_design requires at least one domain object")
        result = []
        allowed = {"aggregate_root", "entity", "value_object", "domain_service", "repository"}
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise EngineeringError("each domain object must be an object")
            item_id = _identifier(raw.get("id"), "domain object id").upper()
            if not re.fullmatch(r"DOM-\d{3}", item_id):
                raise EngineeringError("domain object IDs must use DOM-001 format")
            kind = str(raw.get("kind", ""))
            if kind not in allowed:
                raise EngineeringError(f"unsupported domain object kind for {item_id}: {kind}")
            rules = raw.get("business_rules")
            if not isinstance(rules, list):
                raise EngineeringError(f"business_rules for {item_id} must be an array")
            result.append({
                "id": item_id,
                "name": _text(raw.get("name"), f"name for {item_id}", 120),
                "kind": kind,
                "description": _text(raw.get("description"), f"description for {item_id}", 500),
                "business_rules": [str(value).strip()[:300] for value in rules if str(value).strip()],
                "requirement_ids": _requirement_references(raw.get("requirement_ids"), known_requirements, item_id),
            })
        if len({item["id"] for item in result}) != len(result):
            raise EngineeringError("domain object IDs must be unique")
        return result

    def _link_implementation(
        self, arguments: dict[str, Any], evidence: Mapping[str, Evidence]
    ) -> None:
        if self._state["phase"] != "implementation":
            raise EngineeringError("move to the implementation phase before linking implementation evidence")
        self._require_links(arguments, evidence, implementation=True)
        affected = {str(item.get("requirement_id", "")).upper() for item in arguments.get("links", [])}
        self._state["test_links"] = [
            item for item in self._state["test_links"] if item["requirement_id"] not in affected
        ]
        self._invalidate_decisions("project_acceptance")
        if self._gate("implementation")["passed"]:
            self._state["phase"] = "verification"

    def _link_tests(
        self, arguments: dict[str, Any], evidence: Mapping[str, Evidence]
    ) -> None:
        if self._state["phase"] != "verification":
            raise EngineeringError("move to the verification phase before linking test evidence")
        self._require_links(arguments, evidence, implementation=False)
        self._invalidate_decisions("project_acceptance")
        if self._gate("verification")["passed"]:
            self._state["phase"] = "acceptance"

    def _require_links(
        self,
        arguments: dict[str, Any],
        evidence: Mapping[str, Evidence],
        *,
        implementation: bool,
    ) -> None:
        raw_links = arguments.get("links")
        if not isinstance(raw_links, list) or not raw_links:
            raise EngineeringError("links must contain at least one evidence link")
        known = {item["id"] for item in self._state["requirements"]}
        requirement_by_id = {item["id"]: item for item in self._state["requirements"]}
        parsed = []
        for raw in raw_links:
            if not isinstance(raw, dict):
                raise EngineeringError("each evidence link must be an object")
            requirement_id = str(raw.get("requirement_id", "")).upper()
            if requirement_id not in known:
                raise EngineeringError(f"unknown requirement ID: {requirement_id}")
            evidence_id = str(raw.get("evidence_id", ""))
            record = evidence.get(evidence_id)
            if record is None:
                raise EngineeringError(
                    f"unknown evidence ID: {evidence_id}",
                    candidates=self._evidence_candidates(evidence, implementation=implementation),
                )
            if not record.ok:
                raise EngineeringError(
                    f"evidence {evidence_id} has actual type {record.tool} but did not succeed",
                    candidates=self._evidence_candidates(evidence, implementation=implementation),
                )
            if implementation:
                if record.tool not in {"write_file", "edit_file", "make_directory"}:
                    raise EngineeringError(
                        f"implementation evidence {evidence_id} has actual type {record.tool}; "
                        "expected write_file, edit_file, or make_directory",
                        candidates=self._evidence_candidates(evidence, implementation=True),
                    )
                if not record.changed:
                    raise EngineeringError(
                        f"implementation evidence {evidence_id} has actual type {record.tool} but did not change "
                        "the workspace; reuse the original change evidence",
                        candidates=self._evidence_candidates(evidence, implementation=True),
                    )
                path = _text(raw.get("path"), "implementation path", 500)
                metadata = self._evidence_metadata(evidence_id)
                if metadata:
                    if metadata.get("path") and metadata.get("path") != path:
                        raise EngineeringError(
                            f"implementation evidence {evidence_id} has actual type {record.tool} and belongs "
                            f"to {metadata.get('path')}, not {path}",
                            candidates=self._evidence_candidates(evidence, implementation=True),
                        )
                    if metadata.get("file_hash") != self._file_hash(path):
                        raise EngineeringError(
                            f"implementation evidence {evidence_id} has actual type {record.tool} and is stale "
                            f"because {path} changed afterward",
                            candidates=self._evidence_candidates(evidence, implementation=True),
                        )
                raw_module_ids = raw.get("module_ids")
                if raw_module_ids is None:
                    module_ids = [
                        item["id"] for item in self._state["design_modules"]
                        if requirement_id in item["requirement_ids"]
                    ]
                elif isinstance(raw_module_ids, list):
                    module_ids = [str(value).upper() for value in raw_module_ids]
                else:
                    raise EngineeringError(f"module_ids for {requirement_id} must be an array")
                known_modules = {item["id"] for item in self._state["design_modules"]}
                unknown_modules = set(module_ids) - known_modules
                if unknown_modules:
                    raise EngineeringError(
                        f"implementation link for {requirement_id} maps unknown modules: "
                        + ", ".join(sorted(unknown_modules))
                    )
                if not module_ids:
                    raise EngineeringError(f"implementation link for {requirement_id} requires module_ids")
                module_requirements = {
                    item["id"]: set(item["requirement_ids"])
                    for item in self._state["design_modules"]
                }
                incompatible_modules = [
                    module_id for module_id in module_ids
                    if requirement_id not in module_requirements[module_id]
                ]
                if incompatible_modules:
                    raise EngineeringError(
                        f"implementation link for {requirement_id} maps modules that do not own this requirement: "
                        + ", ".join(incompatible_modules)
                    )
                parsed.append({
                    "requirement_id": requirement_id,
                    "module_ids": list(dict.fromkeys(module_ids)),
                    "path": path,
                    "evidence_id": evidence_id,
                })
            else:
                evidence_kind = str(raw.get("evidence_kind", ""))
                if evidence_kind not in {
                    "unit_test",
                    "integration_test",
                    "performance_test",
                    "security_test",
                    "supporting_test",
                    "static_analysis",
                    "inspection",
                }:
                    raise EngineeringError(
                        f"verification link for {requirement_id} requires evidence_kind"
                    )
                dynamic_test_kinds = {
                    "unit_test", "integration_test", "performance_test", "security_test"
                }
                executed_case_kinds = dynamic_test_kinds | {"supporting_test"}
                test_method = str(raw.get("test_method", ""))
                test_level = str(raw.get("test_level", ""))
                if evidence_kind in dynamic_test_kinds:
                    if test_method not in {"black_box", "white_box"}:
                        raise EngineeringError(
                            f"verification link for {requirement_id} requires test_method black_box or white_box"
                        )
                    if test_level not in {
                        "unit", "integration", "system", "acceptance", "performance", "security"
                    }:
                        raise EngineeringError(f"verification link for {requirement_id} requires test_level")
                else:
                    if test_method:
                        raise EngineeringError(
                            f"{evidence_kind} evidence for {requirement_id} is supporting verification evidence; "
                            "do not classify it as black_box or white_box"
                        )
                    if test_level and test_level != "static":
                        raise EngineeringError(
                            f"{evidence_kind} evidence for {requirement_id} may only use test_level static"
                        )
                    test_level = test_level or "static"
                requirement = requirement_by_id[requirement_id]
                if requirement["kind"] == "functional" and evidence_kind not in {
                    "unit_test",
                    "integration_test",
                }:
                    raise EngineeringError(
                        f"functional requirement {requirement_id} requires unit_test or integration_test evidence"
                    )
                if evidence_kind in {"unit_test", "integration_test"} and not record.verification:
                    raise EngineeringError(
                        f"{evidence_kind} evidence {evidence_id} has actual type {record.tool}; expected a "
                        "successful verification command",
                        candidates=self._evidence_candidates(evidence, implementation=False),
                    )
                if evidence_kind in {"performance_test", "security_test"} and not record.verification:
                    raise EngineeringError(
                        f"{evidence_kind} evidence {evidence_id} has actual type {record.tool}; expected a "
                        "successful verification command",
                        candidates=self._evidence_candidates(evidence, implementation=False),
                    )
                if evidence_kind == "supporting_test" and not record.verification:
                    raise EngineeringError(
                        f"supporting_test evidence {evidence_id} has actual type {record.tool}; expected a "
                        "successful verification command",
                        candidates=self._evidence_candidates(evidence, implementation=False),
                    )
                if evidence_kind == "static_analysis" and (
                    record.tool != "run_command"
                    or not re.search(
                        r"\b(?:ruff|mypy|pylint|flake8|eslint|tsc|compileall|bandit|pip\s+check|npm\s+audit)\b",
                        record.summary,
                        flags=re.IGNORECASE,
                    )
                ):
                    raise EngineeringError(
                        f"static_analysis evidence {evidence_id} has actual type {record.tool}; expected a successful "
                        "lint, type, compile, dependency, or security check",
                        candidates=self._evidence_candidates(evidence, implementation=False),
                    )
                if evidence_kind == "inspection" and record.tool not in {
                    "read_file",
                    "search_text",
                    "list_files",
                    "get_environment",
                }:
                    raise EngineeringError(
                        f"inspection evidence {evidence_id} has actual type {record.tool}; expected read_file, "
                        "search_text, list_files, or get_environment",
                        candidates=self._evidence_candidates(evidence, implementation=False),
                    )
                claim = _text(raw.get("claim"), "verification claim", 500)
                raw_indices = raw.get("criterion_indices")
                if not isinstance(raw_indices, list) or not raw_indices or not all(
                    isinstance(value, int) and not isinstance(value, bool) for value in raw_indices
                ):
                    raise EngineeringError(
                        f"verification link for {requirement_id} requires 1-based criterion_indices"
                    )
                indices = sorted(set(raw_indices))
                criterion_count = len(requirement["acceptance_criteria"])
                if indices[0] < 1 or indices[-1] > criterion_count:
                    raise EngineeringError(
                        f"criterion_indices for {requirement_id} must be between 1 and {criterion_count}"
                    )
                raw_test_case_ids = raw.get("test_case_ids", [])
                if not isinstance(raw_test_case_ids, list) or not all(
                    isinstance(value, str) and value.strip() for value in raw_test_case_ids
                ):
                    raise EngineeringError(
                        f"test_case_ids for {requirement_id} must be an array of non-empty strings"
                    )
                test_case_ids = list(
                    dict.fromkeys(value.strip()[:300] for value in raw_test_case_ids)
                )
                if (
                    self._state.get("structured_test_strategy_required", True)
                    and evidence_kind in executed_case_kinds
                    and not test_case_ids
                ):
                    raise EngineeringError(
                        f"{evidence_kind} verification link for {requirement_id} requires "
                        "test_case_ids from the successful run"
                    )
                if (
                    self._state.get("structured_test_strategy_required", True)
                    and evidence_kind in executed_case_kinds
                ):
                    metadata = self._evidence_metadata(evidence_id) or {}
                    test_run = metadata.get("test_run")
                    executed_cases = test_run.get("cases", []) if isinstance(test_run, dict) else []
                    executed_ids = {
                        str(value)
                        for case in executed_cases
                        if isinstance(case, dict)
                        for value in (case.get("id", ""), case.get("name", ""))
                        if str(value)
                    }
                    if not executed_ids:
                        raise EngineeringError(
                            f"verification evidence {evidence_id} does not list executed test cases; "
                            "rerun unittest with -v and use its exact case IDs"
                        )
                    unmatched_case_ids = [
                        case_id
                        for case_id in test_case_ids
                        if not any(
                            case_id == executed
                            or case_id.endswith(executed)
                            or executed.endswith(case_id)
                            for executed in executed_ids
                        )
                    ]
                    if unmatched_case_ids:
                        raise EngineeringError(
                            f"test_case_ids for {requirement_id} were not executed by evidence {evidence_id}: "
                            + ", ".join(unmatched_case_ids)
                        )
                raw_module_ids = raw.get("module_ids", [])
                if not isinstance(raw_module_ids, list):
                    raise EngineeringError(f"module_ids for {requirement_id} must be an array")
                module_ids = list(dict.fromkeys(str(value).upper() for value in raw_module_ids))
                known_modules = {item["id"] for item in self._state["design_modules"]}
                unknown_modules = set(module_ids) - known_modules
                if unknown_modules:
                    raise EngineeringError(
                        f"verification link for {requirement_id} maps unknown modules: "
                        + ", ".join(sorted(unknown_modules))
                    )
                module_requirements = {
                    item["id"]: set(item["requirement_ids"])
                    for item in self._state["design_modules"]
                }
                incompatible_modules = [
                    module_id
                    for module_id in module_ids
                    if requirement_id not in module_requirements[module_id]
                ]
                if incompatible_modules:
                    raise EngineeringError(
                        f"verification link for {requirement_id} maps modules that do not own this requirement: "
                        + ", ".join(incompatible_modules)
                    )
                if (
                    self._state.get("structured_test_strategy_required", True)
                    and test_method == "white_box"
                    and not module_ids
                ):
                    raise EngineeringError(
                        f"white_box verification link for {requirement_id} requires module_ids"
                    )
                command = str(raw.get("command") or record.summary).strip()[:500]
                parsed.append(
                    {
                        "requirement_id": requirement_id,
                        "command": command,
                        "evidence_id": evidence_id,
                        "evidence_kind": evidence_kind,
                        "test_method": test_method,
                        "test_level": test_level,
                        "module_ids": module_ids,
                        "claim": claim,
                        "criterion_indices": indices,
                        "test_case_ids": test_case_ids,
                        "implementation_fingerprint": self._implementation_fingerprint(),
                    }
                )
        key = "implementation_links" if implementation else "test_links"
        def link_key(item: dict[str, Any]) -> tuple[Any, ...]:
            if implementation:
                return (item["requirement_id"], item.get("path"))
            return (
                item["requirement_id"],
                item.get("command"),
                item.get("test_method", ""),
                tuple(item.get("module_ids", [])),
                tuple(item.get("test_case_ids", [])),
            )

        existing = {link_key(item): item for item in self._state[key]}
        for item in parsed:
            existing[link_key(item)] = item
        self._state[key] = list(existing.values())

    def _evidence_candidates(
        self, evidence: Mapping[str, Evidence], *, implementation: bool
    ) -> list[dict[str, Any]]:
        allowed = (
            {"write_file", "edit_file", "make_directory"}
            if implementation
            else {
                "run_command",
                "read_file",
                "search_text",
                "list_files",
                "get_environment",
            }
        )
        candidates: list[dict[str, Any]] = []
        for evidence_id, record in reversed(list(evidence.items())):
            if not record.ok or record.tool not in allowed:
                continue
            metadata = self._evidence_metadata(evidence_id) or {}
            path = str(metadata.get("path", ""))
            stale = bool(
                implementation
                and path
                and metadata.get("file_hash") != self._file_hash(path)
            )
            candidates.append(
                {
                    "id": evidence_id,
                    "tool": record.tool,
                    "summary": record.summary,
                    "path": path,
                    "valid": not stale and (record.changed if implementation else True),
                    "reason": "file changed after this evidence" if stale else "compatible evidence type",
                }
            )
            if len(candidates) >= 8:
                break
        return candidates

    def _implementation_fingerprint(self) -> str:
        paths = sorted(
            {
                str(item.get("path", ""))
                for item in self._state["implementation_links"]
                if item.get("path")
            }
        )
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.encode("utf-8"))
            digest.update(self._file_hash(path).encode("ascii"))
        return digest.hexdigest()

    def _evidence_metadata(self, evidence_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in reversed(self._state.get("evidence", []))
                if item.get("id") == evidence_id
            ),
            None,
        )

    def _file_hash(self, path: str) -> str:
        if not path:
            return ""
        try:
            target = (self.workspace / path).resolve()
            target.relative_to(self.workspace)
            if not target.is_file():
                return "missing"
            return hashlib.sha256(target.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return "unavailable"

    def _review_summary(self) -> dict[str, Any]:
        requirements = self._state["requirements"]
        implementation_audit = self._implementation_audit()
        stale = sum(
            item.get("implementation_fingerprint") != self._implementation_fingerprint()
            for item in self._state["test_links"]
        )
        current_links = [
            item for item in self._state["test_links"]
            if item.get("implementation_fingerprint") == self._implementation_fingerprint()
        ]
        criteria_total = sum(len(item["acceptance_criteria"]) for item in requirements)
        criteria_covered = sum(
            len({
                index for link in current_links if link["requirement_id"] == requirement["id"]
                for index in link.get("criterion_indices", [])
            })
            for requirement in requirements
        )
        deliverables = sorted(set(implementation_audit["tracked_files"]) | set(
            implementation_audit["changed_files"]
        ))
        return {
            "requirements": len(requirements),
            "design_modules": len(self._state["design_modules"]),
            "implementation_links": len(self._state["implementation_links"]),
            "verification_links": len(self._state["test_links"]),
            "stale_evidence": stale,
            "requirements_covered": len({item["requirement_id"] for item in self._state["implementation_links"]}),
            "modules_completed": implementation_audit["modules_completed"],
            "modules_total": implementation_audit["modules_total"],
            "incomplete_modules": implementation_audit["incomplete_modules"],
            "invalid_module_links": implementation_audit["invalid_module_links"],
            "untracked_files": implementation_audit["untracked_files"],
            "criteria_total": criteria_total,
            "criteria_covered": criteria_covered,
            "black_box_links": sum(item.get("test_method") == "black_box" for item in current_links),
            "white_box_links": sum(item.get("test_method") == "white_box" for item in current_links),
            "test_strategy": self._test_strategy_audit(),
            "deliverables": deliverables,
            "residual_risk": (
                "验收仅覆盖已确认的需求和验收标准；未声明的输入范围、环境差异和新变更不在本次证明范围内。"
            ),
        }

    def _requirements_digest(self) -> str:
        payload = {
            "requirements": self._state["requirements"],
            "assumptions": self._state["assumptions"],
            "actors": self._state.get("actors", []),
            "use_cases": self._state.get("use_cases", []),
            "use_case_relationships": self._state.get("use_case_relationships", []),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _design_digest(self) -> str:
        payload = {
            "design_modules": self._state["design_modules"],
            "uml_classes": self._state.get("uml_classes", []),
            "uml_relationships": self._state.get("uml_relationships", []),
            "sequences": self._state.get("sequences", []),
            "process_flows": self._state.get("process_flows", []),
            "domain_objects": self._state.get("domain_objects", []),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _advance_phase(self, arguments: dict[str, Any]) -> None:
        target = str(arguments.get("target_phase", ""))
        if target not in PHASES:
            raise EngineeringError("target_phase is required")
        current_index = PHASES.index(self._state["phase"])
        target_index = PHASES.index(target)
        if self._state["status"] == "completed" and target_index < current_index:
            decision = next(
                (
                    item
                    for item in reversed(self._state["decisions"])
                    if item.get("key") == "completed_project_change"
                ),
                None,
            )
            if decision is None:
                raise EngineeringError(
                    "completed project replacement requires a completed_project_change user decision"
                )
            choice = decision.get("option_id")
            if choice == "new_workspace":
                raise EngineeringError(
                    "the user chose a new workspace; select another local project instead of replacing this one"
                )
            if choice == "replace_current" and target != "requirements":
                raise EngineeringError(
                    "replace_current must restart from the requirements phase"
                )
            if choice not in {"modify_current", "replace_current"}:
                raise EngineeringError("completed_project_change decision is not actionable")
        if target_index > current_index + 1:
            raise EngineeringError("engineering phases cannot be skipped")
        if target_index > current_index:
            gate = self._gate(PHASES[current_index])
            if not gate["passed"]:
                raise EngineeringError(
                    f"{PHASE_TITLES[PHASES[current_index]]} quality gate failed: "
                    + "; ".join(gate["missing"])
                )
        elif target_index < current_index:
            if target == "requirements":
                self._state["design_modules"] = []
                self._state["uml_classes"] = []
                self._state["uml_relationships"] = []
                self._state["sequences"] = []
                self._state["process_flows"] = []
                self._state["domain_objects"] = []
                self._state["implementation_links"] = []
                self._state["test_links"] = []
                self._invalidate_decisions("requirements_baseline", "design_baseline", "project_acceptance")
            elif target == "design":
                self._state["implementation_links"] = []
                self._state["test_links"] = []
                self._invalidate_decisions("design_baseline", "project_acceptance")
            elif target == "implementation":
                self._state["test_links"] = []
                self._invalidate_decisions("project_acceptance")
            elif target == "verification":
                self._invalidate_decisions("project_acceptance")
        self._state["phase"] = target
        self._state["status"] = "active"

    def _complete_project(self) -> None:
        if self._state["phase"] != "acceptance":
            raise EngineeringError("move to the acceptance phase before completing the project")
        gate = self._gate("acceptance")
        if not gate["passed"]:
            raise EngineeringError("acceptance quality gate failed: " + "; ".join(gate["missing"]))
        self._state["status"] = "completed"

    def _invalidate_decisions(self, *keys: str) -> None:
        invalid = set(keys)
        self._state["decisions"] = [
            item for item in self._state["decisions"] if item.get("key") not in invalid
        ]

    def _phase_status(self, phase: str) -> str:
        current = PHASES.index(self._state["phase"])
        index = PHASES.index(phase)
        if self._state["status"] == "completed" or index < current:
            return "completed"
        if index == current:
            return "awaiting_user" if self._state["status"] == "awaiting_user" else "active"
        return "pending"

    def _gate(self, phase: str) -> dict[str, Any]:
        missing: list[str] = []
        requirements = self._state["requirements"]
        decisions = {item.get("key"): item for item in self._state["decisions"]}
        if phase == "requirements":
            missing.extend(self._requirements_content_missing())
            if not _is_approved(decisions.get("requirements_baseline")):
                missing.append("用户确认需求基线")
        elif phase == "design":
            missing.extend(self._design_content_missing())
            if self._state.get("design_confirmation_required", True) and not _is_approved(
                decisions.get("design_baseline")
            ):
                missing.append("用户确认设计基线")
        elif phase == "implementation":
            missing.extend(self._implementation_traceability_missing())
        elif phase == "verification":
            missing.extend(self._implementation_traceability_missing())
            current_fingerprint = self._implementation_fingerprint()
            for requirement in requirements:
                links = [
                    item
                    for item in self._state["test_links"]
                    if item["requirement_id"] == requirement["id"]
                    and item.get("implementation_fingerprint") == current_fingerprint
                ]
                covered = {
                    index for item in links for index in item.get("criterion_indices", [])
                }
                expected = set(range(1, len(requirement["acceptance_criteria"]) + 1))
                uncovered = expected - covered
                if uncovered:
                    labels = ", ".join(str(index) for index in sorted(uncovered))
                    missing.append(f"{requirement['id']} 缺少或已过期的验收标准证据：{labels}")
            if self._state.get("structured_test_strategy_required", True):
                missing.extend(self._test_strategy_audit()["missing"])
            missing.extend(self._documentation_consistency_missing())
        elif phase == "acceptance":
            verification = self._gate("verification")
            missing.extend(verification["missing"])
            if not _is_approved(decisions.get("project_acceptance")):
                missing.append("用户完成项目验收")
        return {"passed": not missing, "missing": missing}

    def _requirements_content_missing(self) -> list[str]:
        requirements = self._state.get("requirements", [])
        missing: list[str] = []
        if not requirements:
            missing.append("至少定义一个需求")
            return missing
        if any(not item.get("acceptance_criteria") for item in requirements):
            missing.append("每个需求都有验收标准")
        if not self._state.get("use_case_model_required", True):
            return missing
        actors = self._state.get("actors", [])
        use_cases = self._state.get("use_cases", [])
        if not actors:
            missing.append("用例模型至少包含一个参与者")
        if not use_cases:
            missing.append("用例模型至少包含一个完整用例规约")
            return missing
        actor_ids = {item.get("id") for item in actors}
        associated = {
            actor_id for use_case in use_cases for actor_id in use_case.get("actor_ids", [])
        }
        unassociated = sorted(str(value) for value in actor_ids - associated if value)
        if unassociated:
            missing.append("参与者未关联用例：" + "、".join(unassociated))
        for requirement in requirements:
            if requirement.get("kind") != "functional":
                continue
            covered = {
                index
                for use_case in use_cases
                for link in use_case.get("acceptance_links", [])
                if link.get("requirement_id") == requirement.get("id")
                for index in link.get("criterion_indices", [])
            }
            expected = set(range(1, len(requirement.get("acceptance_criteria", [])) + 1))
            uncovered = sorted(expected - covered)
            if uncovered:
                missing.append(
                    f"{requirement['id']} 的验收标准缺少用例覆盖："
                    + "、".join(str(index) for index in uncovered)
                )
        return missing

    def _implementation_traceability_missing(self) -> list[str]:
        audit = self._implementation_audit()
        missing: list[str] = []
        if audit["uncovered_requirements"]:
            missing.append("实现证据覆盖需求：" + ", ".join(audit["uncovered_requirements"]))
        for module in audit["incomplete_modules"]:
            missing.append(
                f"{module['id']} 模块缺少需求实现映射：" + ", ".join(module["missing_requirement_ids"])
            )
        if audit["invalid_module_links"]:
            labels = [
                f"{item['requirement_id']}->{item['module_id']} ({item['path']})"
                for item in audit["invalid_module_links"][:8]
            ]
            missing.append("实现证据映射到不负责该需求的模块：" + "；".join(labels))
        if audit["untracked_files"]:
            missing.append("已修改文件尚未加入模块实现追踪：" + ", ".join(audit["untracked_files"][:12]))
        return missing

    def _test_strategy_audit(self) -> dict[str, Any]:
        current_fingerprint = self._implementation_fingerprint()
        current_links = [
            item
            for item in self._state["test_links"]
            if item.get("implementation_fingerprint") == current_fingerprint
        ]
        dynamic_links = [
            item
            for item in current_links
            if item.get("test_method") in {"black_box", "white_box"}
        ]
        functional_requirements = [
            item for item in self._state["requirements"] if item.get("kind") == "functional"
        ]
        functional_criteria_total = sum(
            len(item.get("acceptance_criteria", [])) for item in functional_requirements
        )
        functional_criteria_covered = 0
        missing_black_box_criteria: list[str] = []
        for requirement in functional_requirements:
            covered = {
                index
                for link in dynamic_links
                if link.get("requirement_id") == requirement["id"]
                and link.get("test_method") == "black_box"
                for index in link.get("criterion_indices", [])
            }
            expected = set(range(1, len(requirement.get("acceptance_criteria", [])) + 1))
            functional_criteria_covered += len(expected & covered)
            missing_black_box_criteria.extend(
                f"{requirement['id']}-AC-{index}"
                for index in sorted(expected - covered)
            )

        functional_ids = {item["id"] for item in functional_requirements}
        core_modules = [
            module
            for module in self._state["design_modules"]
            if functional_ids.intersection(module.get("requirement_ids", []))
        ]
        white_box_module_ids = {
            module_id
            for link in dynamic_links
            if link.get("test_method") == "white_box" and link.get("test_case_ids")
            for module_id in link.get("module_ids", [])
        }
        missing_white_box_modules = [
            module["id"] for module in core_modules if module["id"] not in white_box_module_ids
        ]
        dynamic_links_without_cases = [
            str(item.get("requirement_id", ""))
            for item in dynamic_links
            if not item.get("test_case_ids")
        ]
        static_pattern = re.compile(
            r"(?:stdlib|third.?party|no.?third.?party|dependency|imports?|lint|static|compile|"
            r"标准库|第三方|依赖|导入|静态)",
            flags=re.IGNORECASE,
        )
        misclassified_supporting_links = [
            str(item.get("requirement_id", ""))
            for item in dynamic_links
            if item.get("test_method") == "white_box"
            and static_pattern.search(
                " ".join(
                    [
                        str(item.get("claim", "")),
                        *[str(value) for value in item.get("test_case_ids", [])],
                    ]
                )
            )
        ]
        missing: list[str] = []
        if missing_black_box_criteria:
            missing.append(
                "功能验收标准缺少黑盒测试：" + ", ".join(missing_black_box_criteria[:20])
            )
        if missing_white_box_modules:
            missing.append(
                "核心设计模块缺少业务白盒测试：" + ", ".join(missing_white_box_modules)
            )
        if dynamic_links_without_cases:
            missing.append(
                "动态测试证据缺少真实 test_case_ids："
                + ", ".join(sorted(set(dynamic_links_without_cases)))
            )
        if misclassified_supporting_links:
            missing.append(
                "静态/依赖检查不能计为业务白盒测试："
                + ", ".join(sorted(set(misclassified_supporting_links)))
            )
        run = self._verification_summary().get("latest_run")
        return {
            "required": bool(self._state.get("structured_test_strategy_required", True)),
            "passed": not missing,
            "missing": missing,
            "functional_criteria_total": functional_criteria_total,
            "functional_criteria_black_box_covered": functional_criteria_covered,
            "core_modules_total": len(core_modules),
            "core_modules_white_box_covered": len(core_modules) - len(missing_white_box_modules),
            "missing_black_box_criteria": missing_black_box_criteria,
            "missing_white_box_modules": missing_white_box_modules,
            "dynamic_links_without_cases": dynamic_links_without_cases,
            "misclassified_supporting_links": misclassified_supporting_links,
            "black_box_cases": (
                int(run.get("black_box", {}).get("total", 0)) if isinstance(run, dict) else 0
            ),
            "white_box_cases": (
                int(run.get("white_box", {}).get("total", 0)) if isinstance(run, dict) else 0
            ),
        }

    def _implementation_audit(self) -> dict[str, Any]:
        requirements = {item["id"] for item in self._state["requirements"]}
        modules = {item["id"]: item for item in self._state["design_modules"]}
        links = self._state["implementation_links"]
        mapped_requirements = {item["requirement_id"] for item in links}
        module_rows: list[dict[str, Any]] = []
        invalid_links: list[dict[str, str]] = []
        for link in links:
            requirement_id = str(link.get("requirement_id", ""))
            for module_id in link.get("module_ids", []):
                module = modules.get(module_id)
                if module is not None and requirement_id not in module["requirement_ids"]:
                    invalid_links.append({
                        "requirement_id": requirement_id,
                        "module_id": module_id,
                        "path": str(link.get("path", "")),
                    })
        for module in self._state["design_modules"]:
            required = list(module["requirement_ids"])
            relevant_links = [
                link for link in links
                if module["id"] in link.get("module_ids", [])
                and link.get("requirement_id") in required
            ]
            covered = sorted({str(link["requirement_id"]) for link in relevant_links})
            missing = sorted(set(required) - set(covered))
            paths = sorted({str(link["path"]) for link in relevant_links if link.get("path")})
            module_rows.append({
                "id": module["id"],
                "name": module["name"],
                "required_requirement_ids": required,
                "covered_requirement_ids": covered,
                "missing_requirement_ids": missing,
                "paths": paths,
                "complete": bool(required) and not missing and bool(paths),
            })
        tracked_files = sorted({str(item["path"]) for item in links if item.get("path")})
        changed_files = self._current_changed_files()
        untracked_files = sorted(set(changed_files) - set(tracked_files))
        incomplete = [item for item in module_rows if not item["complete"]]
        return {
            "passed": not (requirements - mapped_requirements) and not incomplete
            and not invalid_links and not untracked_files,
            "modules_total": len(module_rows),
            "modules_completed": len(module_rows) - len(incomplete),
            "modules": module_rows,
            "incomplete_modules": incomplete,
            "uncovered_requirements": sorted(requirements - mapped_requirements),
            "invalid_module_links": invalid_links,
            "tracked_files": tracked_files,
            "changed_files": changed_files,
            "untracked_files": untracked_files,
        }

    def _current_changed_files(self) -> list[str]:
        paths: set[str] = set()
        for record in self._state.get("evidence", []):
            path = str(record.get("path", "")).replace("\\", "/")
            if (
                not record.get("ok")
                or not record.get("changed", True)
                or record.get("tool") not in {"write_file", "edit_file"}
                or not path
                or path.startswith((".yukai/", ".fyk-agent/", ".git/"))
            ):
                continue
            current_hash = self._file_hash(path)
            if current_hash not in {"missing", "unavailable", ""} and record.get("file_hash") == current_hash:
                paths.add(path)
        return sorted(paths)

    def _verification_summary(self) -> dict[str, Any]:
        """Derive UI-facing test runs without duplicating them in the lifecycle state."""
        dynamic_links = [item for item in self._state["test_links"] if item.get("test_method")]
        supporting_links = [item for item in self._state["test_links"] if not item.get("test_method")]
        case_links = [
            item
            for item in self._state["test_links"]
            if item.get("test_method") or item.get("evidence_kind") == "supporting_test"
        ]
        unittest_evidence = [
            item
            for item in self._state.get("evidence", [])
            if item.get("tool") == "run_command"
            and re.search(r"\bunittest\b", str(item.get("command", "")), flags=re.IGNORECASE)
        ]
        latest_evidence = unittest_evidence[-1] if unittest_evidence else None
        latest_run = deepcopy(latest_evidence.get("test_run")) if latest_evidence else None
        discovered = self._discover_unittest_cases()
        if latest_evidence and not isinstance(latest_run, dict):
            total = latest_evidence.get("test_count")
            exact_success = bool(
                latest_evidence.get("ok")
                and isinstance(total, int)
                and total == len(discovered)
            )
            recovered_cases = [
                {**item, "status": "passed" if exact_success else "unknown"}
                for item in discovered
            ]
            latest_run = _summarize_test_cases(
                recovered_cases,
                total=total if isinstance(total, int) else len(recovered_cases),
                command=str(latest_evidence.get("command", "")),
                duration_seconds=None,
                exit_code=latest_evidence.get("exit_code"),
                source="recovered_from_workspace",
            )
        elif isinstance(latest_run, dict):
            latest_run = self._merge_discovered_test_cases(latest_run, discovered)

        if isinstance(latest_run, dict):
            for case in latest_run.get("cases", []):
                traces = []
                matched_links: list[dict[str, Any]] = []
                for link in case_links:
                    explicit_ids = {
                        str(value) for value in link.get("test_case_ids", []) if str(value)
                    }
                    identifiers = {str(case.get("id", "")), str(case.get("name", ""))}
                    explicitly_linked = bool(explicit_ids & identifiers) or any(
                        any(identifier.endswith(value) or value.endswith(identifier) for value in explicit_ids)
                        for identifier in identifiers
                        if identifier
                    )
                    claim = str(link.get("claim", ""))
                    mentioned = bool(
                        case.get("name")
                        and re.search(
                            rf"(?<![A-Za-z0-9_]){re.escape(str(case['name']))}(?![A-Za-z0-9_])",
                            claim,
                        )
                    )
                    if explicitly_linked or mentioned:
                        matched_links.append(link)
                        traces.append(
                            {
                                "requirement_id": link["requirement_id"],
                                "criterion_indices": list(link.get("criterion_indices", [])),
                            }
                        )
                case["traces"] = traces
                linked_methods = {
                    str(link.get("test_method", ""))
                    for link in matched_links
                    if link.get("test_method") in {"black_box", "white_box"}
                }
                if len(linked_methods) == 1:
                    case["method"] = linked_methods.pop()
                    linked_levels = {
                        str(link.get("test_level", ""))
                        for link in matched_links
                        if str(link.get("test_level", ""))
                    }
                    if len(linked_levels) == 1:
                        case["level"] = linked_levels.pop()
                elif any(
                    link.get("evidence_kind") == "supporting_test"
                    for link in matched_links
                ):
                    case["method"] = "supporting"
                    case["level"] = "static"
            counts = _test_status_counts(latest_run.get("cases", []))
            latest_run["classified_cases"] = len(latest_run.get("cases", []))
            latest_run["black_box"] = counts["black_box"]
            latest_run["white_box"] = counts["white_box"]
            latest_run["supporting"] = counts["supporting"]
            latest_run["unclassified"] = counts["unclassified"]

        return {
            "latest_run": latest_run,
            "dynamic_trace_links": len(dynamic_links),
            "supporting_checks": len(supporting_links),
            "supporting_items": [
                {
                    "requirement_id": item["requirement_id"],
                    "kind": item.get("evidence_kind", "inspection"),
                    "claim": item.get("claim", ""),
                    "command": item.get("command", ""),
                    "criterion_indices": list(item.get("criterion_indices", [])),
                }
                for item in supporting_links
            ],
        }

    def _merge_discovered_test_cases(
        self, run: dict[str, Any], discovered: list[dict[str, Any]]
    ) -> dict[str, Any]:
        parsed_cases = [item for item in run.get("cases", []) if isinstance(item, dict)]
        by_name = {str(item.get("name", "")): item for item in parsed_cases}
        by_id = {str(item.get("id", "")): item for item in parsed_cases}
        merged: list[dict[str, Any]] = []
        matched_parsed_ids: set[str] = set()
        for item in discovered:
            parsed = by_id.get(item["id"]) or by_name.get(item["name"])
            combined = {**(parsed or {}), **item}
            if parsed:
                matched_parsed_ids.add(str(parsed.get("id", "")))
                combined["status"] = parsed.get("status", "unknown")
                combined["detail"] = parsed.get("detail", "")
            merged.append(combined)
        known_ids = {str(item["id"]) for item in merged}
        merged.extend(
            item
            for item in parsed_cases
            if str(item.get("id", "")) not in matched_parsed_ids
            and str(item.get("id", "")) not in known_ids
        )
        if merged:
            run["cases"] = merged
            counts = _test_status_counts(merged)
            run["classified_cases"] = len(merged)
            run["black_box"] = counts["black_box"]
            run["white_box"] = counts["white_box"]
            run["supporting"] = counts["supporting"]
            run["unclassified"] = counts["unclassified"]
        return run

    def _discover_unittest_cases(self) -> list[dict[str, Any]]:
        tests_directory = self.workspace / "tests"
        if not tests_directory.is_dir():
            return []
        cases: list[dict[str, Any]] = []
        for path in sorted(tests_directory.rglob("test*.py")):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, UnicodeError, SyntaxError):
                continue
            relative_path = path.relative_to(self.workspace).as_posix()
            module_name = relative_path[:-3].replace("/", ".")
            module_context = ast.get_docstring(tree) or ""
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    context = " ".join(
                        (module_context, node.name, ast.get_docstring(node) or "")
                    )
                    for member in node.body:
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test"):
                            cases.append(
                                _discovered_test_case(
                                    module_name,
                                    node.name,
                                    member,
                                    relative_path,
                                    context,
                                )
                            )
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    cases.append(
                        _discovered_test_case(
                            module_name,
                            "",
                            node,
                            relative_path,
                            module_context,
                        )
                    )
        return cases

    def _design_content_missing(self) -> list[str]:
        missing: list[str] = []
        known_ids = {item["id"] for item in self._state["requirements"]}
        mapped = {
            req_id for module in self._state["design_modules"] for req_id in module["requirement_ids"]
        }
        if not self._state["design_modules"]:
            missing.append("至少定义一个设计模块")
        uncovered = known_ids - mapped
        if uncovered:
            missing.append("设计覆盖需求：" + ", ".join(sorted(uncovered)))
        if not self._state.get("uml_classes"):
            missing.append("至少定义一个 UML 类")
        if not self._state.get("sequences"):
            missing.append("至少定义一个关键业务时序")
        if self._state.get("process_flow_required", True) and not self._state.get("process_flows"):
            missing.append("至少定义一个系统业务流程图")
        if self._state.get("process_flows"):
            functional_ids = {
                item["id"] for item in self._state["requirements"] if item["kind"] == "functional"
            }
            flow_coverage = {
                requirement_id
                for flow in self._state["process_flows"]
                for requirement_id in flow["requirement_ids"]
            }
            uncovered_flows = functional_ids - flow_coverage
            if uncovered_flows:
                missing.append("业务流程图覆盖功能需求：" + ", ".join(sorted(uncovered_flows)))
        if not self._state.get("domain_objects"):
            missing.append("至少定义一个领域对象")
        return missing

    def _documentation_consistency_missing(self) -> list[str]:
        docs_directory = self.workspace / "docs"
        if not docs_directory.is_dir():
            return []
        documents = sorted(docs_directory.rglob("*.md"))
        verification_documents = [
            path
            for path in documents
            if re.search(
                r"(?:test|verification|quality|report|trace|readme|测试|验证|质量|报告|追踪)",
                path.name,
                flags=re.IGNORECASE,
            )
        ]
        if not verification_documents:
            return []
        evidence_records = self._state.get("evidence", [])
        implementation_times = [
            _parse_time(item.get("timestamp"))
            for item in evidence_records
            if item.get("ok")
            and item.get("tool") in {"write_file", "edit_file", "make_directory"}
            and item.get("path")
            and not str(item.get("path")).replace("\\", "/").startswith("docs/")
        ]
        latest_change = max((value for value in implementation_times if value), default=None)
        missing: list[str] = []
        if latest_change is not None:
            stale = [
                str(path.relative_to(self.workspace)).replace("\\", "/")
                for path in verification_documents
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < latest_change
            ]
            if stale:
                missing.append("用户文档早于最新代码/测试修改，请更新：" + ", ".join(stale[:5]))

        successful_counts = [
            int(item["test_count"])
            for item in evidence_records
            if item.get("ok") and isinstance(item.get("test_count"), int)
        ]
        if successful_counts:
            actual = max(successful_counts)
            mismatches: list[str] = []
            for path in verification_documents:
                try:
                    claimed = _document_test_counts(path.read_text(encoding="utf-8"))
                except OSError:
                    continue
                if claimed and any(value != actual for value in claimed):
                    relative = str(path.relative_to(self.workspace)).replace("\\", "/")
                    mismatches.append(f"{relative} 声明 {sorted(claimed)}，实际 {actual}")
            if mismatches:
                missing.append("文档测试数量与最近成功测试不一致：" + "；".join(mismatches[:5]))
        return missing

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") not in {1, 2, 3, 4, 5} or raw.get("phase") not in PHASES:
                return _default_state()
            state = _default_state()
            for key in state:
                if key in raw:
                    state[key] = raw[key]
            if raw.get("version") == 1:
                state["design_confirmation_required"] = False
            if raw.get("version") in {1, 2}:
                state["process_flow_required"] = False
            if raw.get("version") in {1, 2, 3}:
                state["use_case_model_required"] = False
            if raw.get("version") in {1, 2, 3, 4}:
                # Existing projects keep their original acceptance semantics. Their
                # strategy audit is still visible, but only newly created projects
                # are blocked by the stricter black/white-box quality gate.
                state["structured_test_strategy_required"] = False
            state["version"] = 5
            return state
        except (OSError, json.JSONDecodeError):
            return _default_state()

    def _persist(self, *, write_documents: bool = True) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        if write_documents:
            self._write_documents()

    def _write_documents(self) -> None:
        requirements = ["# 软件需求规格说明", "", f"项目：{self._state['project_title'] or '未命名项目'}", ""]
        for item in self._state["requirements"]:
            requirements.extend(
                [
                    f"## {item['id']} {item['title']}",
                    "",
                    item["description"],
                    "",
                    "验收标准：",
                    *[f"- {criterion}" for criterion in item["acceptance_criteria"]],
                    "",
                ]
            )
        if self._state["assumptions"]:
            requirements.extend(
                [
                    "## 待确认的默认决策与假设",
                    "",
                    *[f"- {item}" for item in self._state["assumptions"]],
                    "",
                ]
            )
        requirements.extend(["# 用例模型", "", "## 参与者", ""])
        if self._state.get("actors"):
            requirements.extend(
                f"- **{item['id']} {item['name']}**：{item['description']}"
                for item in self._state["actors"]
            )
        else:
            requirements.append("旧需求基线未记录参与者。")
        requirements.extend(["", "## 用例规约", ""])
        actor_names = {item["id"]: item["name"] for item in self._state.get("actors", [])}
        for use_case in self._state.get("use_cases", []):
            requirements.extend(
                [
                    f"### {use_case['id']} {use_case['name']}",
                    "",
                    f"- 目标：{use_case['goal']}",
                    "- 参与者：" + "、".join(
                        f"{actor_id} {actor_names.get(actor_id, actor_id)}"
                        for actor_id in use_case.get("actor_ids", [])
                    ),
                    "- 前置条件：" + ("；".join(use_case.get("preconditions", [])) or "无"),
                    "- 后置条件：" + "；".join(use_case.get("postconditions", [])),
                    "- 需求追踪：" + "；".join(
                        f"{link['requirement_id']} 验收标准 {','.join(str(index) for index in link['criterion_indices'])}"
                        for link in use_case.get("acceptance_links", [])
                    ),
                    "",
                    "主成功场景：",
                    *[
                        f"{index}. {step}"
                        for index, step in enumerate(use_case.get("main_flow", []), start=1)
                    ],
                    "",
                ]
            )
            if use_case.get("alternative_flows"):
                requirements.extend(["备选/异常流程：", ""])
                for alternative in use_case["alternative_flows"]:
                    requirements.append(
                        f"- **{alternative['id']}（{alternative['condition']}）**："
                        + "；".join(alternative.get("steps", []))
                    )
                requirements.append("")
        relationships = self._state.get("use_case_relationships", [])
        requirements.extend(["## 用例关系", ""])
        if relationships:
            requirements.extend(
                f"- {item['from']} --{item['type']}--> {item['to']}"
                + (f"：{item['label']}" if item.get("label") else "")
                for item in relationships
            )
        else:
            requirements.append("本需求基线没有需要单独建模的 include、extend 或泛化关系。")
        requirements.append("")
        design = ["# 结构化设计说明", ""]
        for module in self._state["design_modules"]:
            design.extend(
                [
                    f"## {module['id']} {module['name']}",
                    "",
                    module["responsibility"],
                    "",
                    "需求映射：" + ", ".join(module["requirement_ids"]),
                    "",
                    "接口：" + ("；".join(module["interfaces"]) or "暂无"),
                    "依赖：" + (", ".join(module.get("dependencies", [])) or "无"),
                    "",
                ]
            )
        design.extend(["# UML 类模型", ""])
        for item in self._state.get("uml_classes", []):
            design.extend([
                f"## {item['id']} {item['name']} ({item.get('stereotype', 'class')})",
                "",
                "属性：" + ("；".join(item.get("attributes", [])) or "暂无"),
                "",
                "方法：" + ("；".join(item.get("methods", [])) or "暂无"),
                "",
            ])
        design.extend(["# 关键业务时序", ""])
        for sequence in self._state.get("sequences", []):
            design.extend([f"## {sequence['id']} {sequence['name']}", ""])
            design.extend([
                f"- {step['from']} → {step['to']}：{step['message']}"
                for step in sequence.get("steps", [])
            ])
            design.append("")
        design.extend(["# 系统业务流程图", ""])
        for flow in self._state.get("process_flows", []):
            design.extend(
                [
                    f"## {flow['id']} {flow['name']}",
                    "",
                    "需求映射：" + ", ".join(flow.get("requirement_ids", [])),
                    "",
                    "```mermaid",
                    _process_flow_mermaid(flow),
                    "```",
                    "",
                ]
            )
        design.extend(["# 领域模型", ""])
        for item in self._state.get("domain_objects", []):
            design.extend([
                f"## {item['id']} {item['name']} ({item['kind']})",
                "",
                item["description"],
                "",
                *[f"- {rule}" for rule in item.get("business_rules", [])],
                "",
            ])
        traceability = [
            "# 需求追踪矩阵",
            "",
            "| 需求 | 设计模块 | 实现文件 | 测试命令 |",
            "| --- | --- | --- | --- |",
        ]
        for requirement in self._state["requirements"]:
            req_id = requirement["id"]
            modules = [item["id"] for item in self._state["design_modules"] if req_id in item["requirement_ids"]]
            paths = [item["path"] for item in self._state["implementation_links"] if req_id == item["requirement_id"]]
            commands = [
                f"{item.get('test_method') or 'supporting'}/{item.get('evidence_kind', 'verification')}: "
                f"{item.get('claim') or '已验证'} ({item.get('command', '')})"
                for item in self._state["test_links"]
                if req_id == item["requirement_id"]
            ]
            traceability.append(
                f"| {req_id} | {', '.join(modules) or '-'} | {', '.join(paths) or '-'} | {', '.join(commands) or '-'} |"
            )
        reports = {
            "requirements.md": requirements,
            "design.md": design,
            "traceability.md": traceability,
        }
        for name, lines in reports.items():
            (self.directory / name).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _default_state() -> dict[str, Any]:
    return {
        "version": 5,
        "project_title": "",
        "phase": "requirements",
        "status": "active",
        "requirements": [],
        "assumptions": [],
        "actors": [],
        "use_cases": [],
        "use_case_relationships": [],
        "use_case_model_required": True,
        "design_modules": [],
        "uml_classes": [],
        "uml_relationships": [],
        "sequences": [],
        "process_flows": [],
        "process_flow_required": True,
        "domain_objects": [],
        "design_confirmation_required": True,
        "structured_test_strategy_required": True,
        "implementation_links": [],
        "test_links": [],
        "decisions": [],
        "evidence": [],
        "pending_question": None,
        "updated_at": _now(),
    }


def _process_flow_mermaid(flow: Mapping[str, Any]) -> str:
    direction = str(flow.get("direction", "TD"))
    lines = [f"flowchart {direction}"]
    for node in flow.get("nodes", []):
        node_id = str(node.get("id", "NODE"))
        label = re.sub(r'[\n\r"{}\[\]|]', " ", str(node.get("label", ""))).strip()
        node_type = node.get("type")
        if node_type in {"start", "end"}:
            lines.append(f'    {node_id}(["{label}"])')
        elif node_type == "decision":
            lines.append(f'    {node_id}{{"{label}"}}')
        elif node_type == "input_output":
            lines.append(f'    {node_id}[/"{label}"/]')
        else:
            lines.append(f'    {node_id}["{label}"]')
    for edge in flow.get("edges", []):
        label = re.sub(r'[\n\r"{}\[\]|]', " ", str(edge.get("label", ""))).strip()
        connector = f" -->|{label}| " if label else " --> "
        lines.append(f"    {edge.get('from')}{connector}{edge.get('to')}")
    return "\n".join(lines)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}", value):
        raise EngineeringError(f"{label} must be a safe identifier")
    return value


def _text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EngineeringError(f"{label} must be non-empty")
    text = " ".join(value.split())
    if len(text) > limit:
        raise EngineeringError(f"{label} exceeds {limit} characters")
    return text


def _text_array(
    value: Any, label: str, *, allow_empty: bool = False
) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise EngineeringError(f"{label} must be {qualifier}")
    return [_text(item, label, 500) for item in value]


def _requirement_references(value: Any, known: set[str], label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise EngineeringError(f"{label} must map at least one requirement")
    mapped = [str(item).upper() for item in value]
    unknown = set(mapped) - known
    if unknown:
        raise EngineeringError(f"{label} maps unknown requirements: {', '.join(sorted(unknown))}")
    return list(dict.fromkeys(mapped))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_approved(decision: dict[str, Any] | None) -> bool:
    return bool(decision and str(decision.get("option_id", "")).lower() in APPROVAL_OPTION_IDS)


def _parse_unittest_run(command: str, result: dict[str, Any]) -> dict[str, Any] | None:
    if not re.search(r"\bunittest\b", command, flags=re.IGNORECASE):
        return None
    output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    cases: list[dict[str, Any]] = []
    case_pattern = re.compile(
        r"^\s*(?P<name>test\S+)\s+\((?P<qualified>[^)]+)\)\s+\.\.\.\s+"
        r"(?P<result>ok|FAIL|ERROR|skipped\s+.+|expected failure|unexpected success)\s*$",
        flags=re.IGNORECASE,
    )
    for line in output.splitlines():
        match = case_pattern.match(line)
        if not match:
            continue
        raw_status = match.group("result").lower()
        if raw_status == "ok" or raw_status == "expected failure":
            status = "passed"
        elif raw_status.startswith("skipped"):
            status = "skipped"
        elif raw_status == "error":
            status = "error"
        else:
            status = "failed"
        qualified = match.group("qualified").strip()
        cases.append(
            {
                "id": qualified,
                "name": match.group("name"),
                "suite": qualified.rsplit(".", 1)[0] if "." in qualified else qualified,
                "path": "",
                "line": None,
                "method": "unclassified",
                "level": "unclassified",
                "purpose": _humanize_test_name(match.group("name")),
                "status": status,
                "detail": match.group("result") if status == "skipped" else "",
            }
        )
    summary = re.findall(
        r"\bRan\s+(\d+)\s+tests?\s+in\s+([0-9.]+)s",
        output,
        flags=re.IGNORECASE,
    )
    total = int(summary[-1][0]) if summary else (_test_count(result) or len(cases))
    duration = float(summary[-1][1]) if summary else None
    failure_count = _unittest_summary_count(output, "failures")
    error_count = _unittest_summary_count(output, "errors")
    skipped_count = _unittest_summary_count(output, "skipped")
    if cases:
        case_statuses = _test_status_counts(cases)["statuses"]
        failure_count = max(failure_count, case_statuses["failed"])
        error_count = max(error_count, case_statuses["error"])
        skipped_count = max(skipped_count, case_statuses["skipped"])
    passed = max(total - failure_count - error_count - skipped_count, 0)
    return {
        "framework": "unittest",
        "command": command[:1000],
        "status": "passed" if result.get("ok") else "failed",
        "total": total,
        "passed": passed,
        "failed": failure_count,
        "errors": error_count,
        "skipped": skipped_count,
        "duration_seconds": duration,
        "exit_code": result.get("exit_code"),
        "source": "command_output",
        "classified_cases": len(cases),
        "black_box": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "unknown": 0},
        "white_box": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "unknown": 0},
        "supporting": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "unknown": 0},
        "unclassified": {"total": len(cases), "passed": passed, "failed": failure_count, "errors": error_count, "skipped": skipped_count, "unknown": 0},
        "cases": cases,
    }


def _unittest_summary_count(output: str, label: str) -> int:
    matches = re.findall(rf"\b{re.escape(label)}=(\d+)\b", output, flags=re.IGNORECASE)
    return int(matches[-1]) if matches else 0


def _discovered_test_case(
    module_name: str,
    class_name: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    relative_path: str,
    context: str,
) -> dict[str, Any]:
    normalized = context.lower().replace("-", "_").replace(" ", "_")
    if "黑盒" in context or "black_box" in normalized or "blackbox" in normalized:
        method = "black_box"
        level = "system"
    elif "白盒" in context or "white_box" in normalized or "whitebox" in normalized:
        method = "white_box"
        level = "unit"
    else:
        method = "unclassified"
        level = "unclassified"
    qualified = ".".join(value for value in (module_name, class_name, node.name) if value)
    return {
        "id": qualified,
        "name": node.name,
        "suite": class_name or module_name,
        "path": relative_path,
        "line": node.lineno,
        "method": method,
        "level": level,
        "purpose": (ast.get_docstring(node) or _humanize_test_name(node.name))[:300],
        "status": "unknown",
        "detail": "",
    }


def _humanize_test_name(name: str) -> str:
    return name.removeprefix("test_").replace("_", " ")


def _test_status_counts(cases: list[dict[str, Any]]) -> dict[str, Any]:
    methods = {
        "black_box": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "unknown": 0},
        "white_box": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "unknown": 0},
        "supporting": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "unknown": 0},
        "unclassified": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "unknown": 0},
    }
    statuses = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "unknown": 0}
    for case in cases:
        method = str(case.get("method", "unclassified"))
        if method not in methods:
            method = "unclassified"
        status = str(case.get("status", "unknown"))
        if status not in statuses:
            status = "unknown"
        statuses[status] += 1
        methods[method]["total"] += 1
        method_status = "errors" if status == "error" else status
        methods[method][method_status] += 1
    return {**methods, "statuses": statuses}


def _summarize_test_cases(
    cases: list[dict[str, Any]],
    *,
    total: int,
    command: str,
    duration_seconds: float | None,
    exit_code: Any,
    source: str,
) -> dict[str, Any]:
    counts = _test_status_counts(cases)
    statuses = counts["statuses"]
    return {
        "framework": "unittest",
        "command": command[:1000],
        "status": "passed" if exit_code == 0 else "failed",
        "total": total,
        "passed": statuses["passed"],
        "failed": statuses["failed"],
        "errors": statuses["error"],
        "skipped": statuses["skipped"],
        "duration_seconds": duration_seconds,
        "exit_code": exit_code,
        "source": source,
        "classified_cases": len(cases),
        "black_box": counts["black_box"],
        "white_box": counts["white_box"],
        "supporting": counts["supporting"],
        "unclassified": counts["unclassified"],
        "cases": cases,
    }


def _test_count(result: dict[str, Any]) -> int | None:
    output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    matches = re.findall(r"\bRan\s+(\d+)\s+tests?\b", output, flags=re.IGNORECASE)
    if not matches:
        matches = re.findall(
            r"\b(\d+)\s+(?:tests?|passed)\b", output, flags=re.IGNORECASE
        )
    return int(matches[-1]) if matches else None


def _document_test_counts(text: str) -> set[int]:
    patterns = (
        r"\bRan\s+(\d+)\s+tests?\b",
        r"(\d+)\s*个测试(?:全部)?通过",
        r"(?:共|总计|通过)\s*(\d+)\s*(?:个|项)?测试",
    )
    return {
        int(match)
        for pattern in patterns
        for match in re.findall(pattern, text, flags=re.IGNORECASE)
    }


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _actual_evidence_from_error(error: str) -> dict[str, str] | None:
    match = re.search(r"evidence\s+(\S+)\s+has actual type\s+(\S+)", error)
    if not match:
        return None
    return {"id": match.group(1).rstrip(";"), "tool": match.group(2).rstrip(";")}


def _next_action(error: str, phase: str) -> str:
    if "FR-001 or NFR-001" in error:
        return "Retry define_requirements with IDs like FR-001 and NFR-001 matching requirement kind."
    if "MOD-001" in error:
        return "Retry define_design with module IDs like MOD-001 and MOD-002."
    if "criterion_indices" in error:
        return "Retry link_tests with evidence_kind, claim, and 1-based criterion_indices for each requirement."
    if "inspection evidence" in error:
        return "Run read_file, search_text, list_files, or get_environment, then link that evidence as inspection."
    if "actual type" in error or "unknown evidence ID" in error:
        return "Choose a compatible candidate_evidence entry, or run the required tool and use its returned evidence_id."
    if "completed_project_change" in error:
        return "Ask the user whether to modify this project, replace it, or select a new workspace before rollback."
    if "unknown affected requirement IDs" in error:
        return (
            "Use only requirement IDs already present in engineering.requirements; before define_requirements, "
            "retry with affected_requirement_ids=[]."
        )
    if "successful verification command" in error:
        return "Run the relevant test command successfully, then link its evidence with the matching test evidence_kind."
    if "move to the" in error:
        return f"Inspect engineering.phase (currently {phase}); phases normally advance automatically after a complete gate."
    if "quality gate failed" in error:
        return "Satisfy every item in engineering.phases[].gate.missing before continuing."
    return "Inspect the returned engineering state and correct only the rejected action."
