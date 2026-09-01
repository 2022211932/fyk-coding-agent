from __future__ import annotations

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
                            },
                            "required": ["id", "name", "responsibility", "requirement_ids"],
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
                                        "static_analysis",
                                        "inspection",
                                    ],
                                    "description": "Required for link_tests. Functional requirements need unit_test or integration_test.",
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
                        "description": "Use requirements_baseline or project_acceptance for those mandatory gates.",
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
                    "affected_requirement_ids": {"type": "array", "items": {"type": "string"}},
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
            return (
                "项目已完成并通过用户验收。\n\n"
                f"- 需求基线：{functional} 项功能需求，{non_functional} 项非功能需求\n"
                f"- 设计模块：{len(self._state['design_modules'])} 个\n"
                f"- 实现追踪：{len(self._state['implementation_links'])} 条\n"
                f"- 验证追踪：{len(self._state['test_links'])} 条（{verification}）\n\n"
                "剩余风险：当前结论仅覆盖已确认需求及其验收标准；输入范围、运行环境或未声明约束发生变化时需要重新评估。"
            )

    def payload(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_stale_verification()
            value = deepcopy(self._state)
            value["evidence_count"] = len(value.get("evidence", []))
            value.pop("evidence", None)
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

    def _refresh_stale_verification(self) -> None:
        if (
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

    def system_context(self) -> str:
        state = self.payload()
        compact = {
            "phase": state["phase"],
            "status": state["status"],
            "project_title": state["project_title"],
            "requirements": state["requirements"],
            "assumptions": state["assumptions"],
            "design_modules": state["design_modules"],
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
            "to approve the requirements baseline with decision_key=requirements_baseline. Before completing, "
            "ask for project acceptance with decision_key=project_acceptance. Ask only about choices that "
            "materially change scope, architecture, constraints, or acceptance; inspect discoverable facts yourself. "
            "A user question must be the last action of the turn. Phases advance automatically after their "
            "gate passes, so inspect the returned engineering.phase before attempting advance_phase. For every "
            "verification link, state the evidence_kind, the exact claim, and which 1-based acceptance criteria "
            "it proves. Inspection evidence only comes from read_file, search_text, list_files, or "
            "get_environment; never label run_command as inspection. Record every material default in the "
            "assumptions field of define_requirements so it appears on the baseline review card. The engineering "
            "workspace may be changed only during implementation. In verification, only recognized test, lint, "
            "type-check, compile, dependency, or build commands are allowed. If project files or user documents "
            "need changes, move back to implementation and refresh their evidence before testing again. When a "
            "completed workspace receives a new project or change request, first ask for decision_key="
            "completed_project_change with modify_current, replace_current, and new_workspace options. "
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
                if decision_key in {"requirements_baseline", "project_acceptance"} and not any(
                    item["id"].lower() in APPROVAL_OPTION_IDS for item in options
                ):
                    raise EngineeringError(
                        f"{decision_key} options must include an approval ID such as approve"
                    )
                if decision_key == "requirements_baseline":
                    if not self._state["requirements"] or any(
                        not item.get("acceptance_criteria") for item in self._state["requirements"]
                    ):
                        raise EngineeringError(
                            "define complete requirements and acceptance criteria before requesting baseline approval"
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
                        "digest": self._requirements_digest(),
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
            decision = {
                "key": pending["decision_key"],
                "question_id": question_id,
                "option_id": option_id,
                "option_label": option["label"],
                "answer": normalized_answer,
                "baseline_digest": pending.get("baseline_review", {}).get("digest", ""),
                "decided_at": _now(),
            }
            self._state["decisions"] = [
                item for item in self._state["decisions"] if item.get("key") != decision["key"]
            ] + [decision]
            self._state["pending_question"] = None
            self._state["status"] = (
                "completed" if decision["key"] == "completed_project_change" else "active"
            )
            if decision["key"] == "requirements_baseline" and _is_approved(decision):
                gate = self._gate("requirements")
                if gate["passed"]:
                    self._state["phase"] = "design"
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
        self._state["project_title"] = str(arguments.get("project_title") or self._state["project_title"]).strip()[:120]
        self._state["assumptions"] = assumptions
        self._state["requirements"] = requirements
        self._state["design_modules"] = []
        self._state["implementation_links"] = []
        self._state["test_links"] = []
        self._invalidate_decisions("requirements_baseline", "project_acceptance")
        pending = self._state.get("pending_question")
        if isinstance(pending, dict) and pending.get("decision_key") == "requirements_baseline":
            self._state["pending_question"] = None
        self._state["status"] = "active"

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
            modules.append(
                {
                    "id": item_id,
                    "name": _text(raw.get("name"), f"name for {item_id}", 120),
                    "responsibility": _text(raw.get("responsibility"), f"responsibility for {item_id}", 800),
                    "requirement_ids": list(dict.fromkeys(mapped)),
                    "interfaces": [str(value).strip()[:300] for value in interfaces if str(value).strip()],
                }
            )
        if len({item["id"] for item in modules}) != len(modules):
            raise EngineeringError("module IDs must be unique")
        self._state["design_modules"] = modules
        self._state["implementation_links"] = []
        self._state["test_links"] = []
        self._invalidate_decisions("project_acceptance")
        if self._gate("design")["passed"]:
            self._state["phase"] = "implementation"

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
                parsed.append({"requirement_id": requirement_id, "path": path, "evidence_id": evidence_id})
            else:
                evidence_kind = str(raw.get("evidence_kind", ""))
                if evidence_kind not in {
                    "unit_test",
                    "integration_test",
                    "performance_test",
                    "security_test",
                    "static_analysis",
                    "inspection",
                }:
                    raise EngineeringError(
                        f"verification link for {requirement_id} requires evidence_kind"
                    )
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
                command = str(raw.get("command") or record.summary).strip()[:500]
                parsed.append(
                    {
                        "requirement_id": requirement_id,
                        "command": command,
                        "evidence_id": evidence_id,
                        "evidence_kind": evidence_kind,
                        "claim": claim,
                        "criterion_indices": indices,
                        "implementation_fingerprint": self._implementation_fingerprint(),
                    }
                )
        key = "implementation_links" if implementation else "test_links"
        existing = {
            (item["requirement_id"], item.get("path") or item.get("command")): item
            for item in self._state[key]
        }
        for item in parsed:
            existing[(item["requirement_id"], item.get("path") or item.get("command"))] = item
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
        stale = sum(
            item.get("implementation_fingerprint") != self._implementation_fingerprint()
            for item in self._state["test_links"]
        )
        return {
            "requirements": len(requirements),
            "design_modules": len(self._state["design_modules"]),
            "implementation_links": len(self._state["implementation_links"]),
            "verification_links": len(self._state["test_links"]),
            "stale_evidence": stale,
            "residual_risk": (
                "验收仅覆盖已确认的需求和验收标准；未声明的输入范围、环境差异和新变更不在本次证明范围内。"
            ),
        }

    def _requirements_digest(self) -> str:
        payload = {
            "requirements": self._state["requirements"],
            "assumptions": self._state["assumptions"],
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
                self._state["implementation_links"] = []
                self._state["test_links"] = []
                self._invalidate_decisions("requirements_baseline", "project_acceptance")
            elif target == "design":
                self._state["implementation_links"] = []
                self._state["test_links"] = []
                self._invalidate_decisions("project_acceptance")
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
        known_ids = {item["id"] for item in requirements}
        decisions = {item.get("key"): item for item in self._state["decisions"]}
        if phase == "requirements":
            if not requirements:
                missing.append("至少定义一个需求")
            if any(not item.get("acceptance_criteria") for item in requirements):
                missing.append("每个需求都有验收标准")
            if not _is_approved(decisions.get("requirements_baseline")):
                missing.append("用户确认需求基线")
        elif phase == "design":
            mapped = {
                req_id for module in self._state["design_modules"] for req_id in module["requirement_ids"]
            }
            if not self._state["design_modules"]:
                missing.append("至少定义一个设计模块")
            uncovered = known_ids - mapped
            if uncovered:
                missing.append("设计覆盖需求：" + ", ".join(sorted(uncovered)))
        elif phase == "implementation":
            mapped = {item["requirement_id"] for item in self._state["implementation_links"]}
            uncovered = known_ids - mapped
            if uncovered:
                missing.append("实现证据覆盖需求：" + ", ".join(sorted(uncovered)))
        elif phase == "verification":
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
            missing.extend(self._documentation_consistency_missing())
        elif phase == "acceptance":
            verification = self._gate("verification")
            missing.extend(verification["missing"])
            if not _is_approved(decisions.get("project_acceptance")):
                missing.append("用户完成项目验收")
        return {"passed": not missing, "missing": missing}

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
            if not isinstance(raw, dict) or raw.get("version") != 1 or raw.get("phase") not in PHASES:
                return _default_state()
            state = _default_state()
            for key in state:
                if key in raw:
                    state[key] = raw[key]
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
                    "",
                ]
            )
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
                f"{item.get('evidence_kind', 'verification')}: "
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
        "version": 1,
        "project_title": "",
        "phase": "requirements",
        "status": "active",
        "requirements": [],
        "assumptions": [],
        "design_modules": [],
        "implementation_links": [],
        "test_links": [],
        "decisions": [],
        "evidence": [],
        "pending_question": None,
        "updated_at": _now(),
    }


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_approved(decision: dict[str, Any] | None) -> bool:
    return bool(decision and str(decision.get("option_id", "")).lower() in APPROVAL_OPTION_IDS)


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
    if "successful verification command" in error:
        return "Run the relevant test command successfully, then link its evidence with the matching test evidence_kind."
    if "move to the" in error:
        return f"Inspect engineering.phase (currently {phase}); phases normally advance automatically after a complete gate."
    if "quality gate failed" in error:
        return "Satisfy every item in engineering.phases[].gate.missing before continuing."
    return "Inspect the returned engineering state and correct only the rejected action."
