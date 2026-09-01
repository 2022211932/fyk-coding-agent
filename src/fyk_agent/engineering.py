from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
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
    pass


class EngineeringWorkflow:
    """Persistent, evidence-gated software-engineering lifecycle for one workspace."""

    update_schema = {
        "type": "function",
        "function": {
            "name": "update_engineering_state",
            "description": (
                "Update Yukai-SE's project-level engineering lifecycle. Requirements need IDs and "
                "acceptance criteria; design modules map requirements; implementation and tests must "
                "cite evidence IDs from successful tools. Use advance_phase only after its quality gate passes."
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
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "kind": {"type": "string", "enum": ["functional", "non_functional"]},
                                "description": {"type": "string"},
                                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "title", "kind", "description", "acceptance_criteria"],
                            "additionalProperties": False,
                        },
                    },
                    "modules": {
                        "type": "array",
                        "maxItems": 40,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
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
                        "items": {
                            "type": "object",
                            "properties": {
                                "requirement_id": {"type": "string"},
                                "path": {"type": "string"},
                                "command": {"type": "string"},
                                "evidence_id": {"type": "string"},
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

    def payload(self) -> dict[str, Any]:
        with self._lock:
            value = deepcopy(self._state)
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

    def system_context(self) -> str:
        state = self.payload()
        compact = {
            "phase": state["phase"],
            "status": state["status"],
            "project_title": state["project_title"],
            "requirements": state["requirements"],
            "design_modules": state["design_modules"],
            "implementation_links": state["implementation_links"],
            "test_links": state["test_links"],
            "decisions": state["decisions"],
            "pending_question": state["pending_question"],
            "quality_gates": {phase["id"]: phase["gate"] for phase in state["phases"]},
        }
        return (
            "\n\nYukai-SE software-engineering mode is enabled.\n"
            "Follow the lifecycle requirements -> design -> implementation -> verification -> acceptance. "
            "Use update_engineering_state to maintain traceability. Before moving to design, ask the user "
            "to approve the requirements baseline with decision_key=requirements_baseline. Before completing, "
            "ask for project acceptance with decision_key=project_acceptance. Ask only about choices that "
            "materially change scope, architecture, constraints, or acceptance; inspect discoverable facts yourself. "
            "A user question must be the last action of the turn.\n"
            "Current engineering state:\n"
            + json.dumps(compact, ensure_ascii=False, default=str)
        )

    def update(
        self, arguments: dict[str, Any], evidence: Mapping[str, Evidence]
    ) -> dict[str, Any]:
        with self._lock:
            try:
                action = str(arguments.get("action", ""))
                if action == "define_requirements":
                    self._define_requirements(arguments)
                elif action == "define_design":
                    self._define_design(arguments)
                elif action == "link_implementation":
                    self._link_implementation(arguments, evidence)
                elif action == "link_tests":
                    self._link_tests(arguments, evidence)
                elif action == "advance_phase":
                    self._advance_phase(arguments)
                elif action == "complete_project":
                    self._complete_project()
                else:
                    raise EngineeringError(f"unknown engineering action: {action}")
                self._state["updated_at"] = _now()
                self._persist()
                return {"ok": True, "engineering": self.payload()}
            except EngineeringError as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "error_type": "engineering_gate_failed",
                    "engineering": self.payload(),
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
                        }
                    )
                if len({item["id"] for item in options}) != len(options):
                    raise EngineeringError("option IDs must be unique")
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
            decision = {
                "key": pending["decision_key"],
                "question_id": question_id,
                "option_id": option_id,
                "option_label": option["label"],
                "answer": answer.strip()[:1000],
                "decided_at": _now(),
            }
            self._state["decisions"] = [
                item for item in self._state["decisions"] if item.get("key") != decision["key"]
            ] + [decision]
            self._state["pending_question"] = None
            self._state["status"] = "active"
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
        self._state["project_title"] = str(arguments.get("project_title") or self._state["project_title"]).strip()[:120]
        self._state["requirements"] = requirements
        self._state["design_modules"] = []
        self._state["implementation_links"] = []
        self._state["test_links"] = []
        self._invalidate_decisions("requirements_baseline", "project_acceptance")
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

    def _link_tests(
        self, arguments: dict[str, Any], evidence: Mapping[str, Evidence]
    ) -> None:
        if self._state["phase"] != "verification":
            raise EngineeringError("move to the verification phase before linking test evidence")
        self._require_links(arguments, evidence, implementation=False)
        self._invalidate_decisions("project_acceptance")

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
                raise EngineeringError(f"unknown evidence ID: {evidence_id}")
            if not record.ok:
                raise EngineeringError(f"evidence {evidence_id} did not succeed")
            if implementation:
                if record.tool not in {"write_file", "edit_file", "make_directory"}:
                    raise EngineeringError(f"implementation evidence {evidence_id} must be a successful change tool")
                path = _text(raw.get("path"), "implementation path", 500)
                parsed.append({"requirement_id": requirement_id, "path": path, "evidence_id": evidence_id})
            else:
                if not record.verification:
                    raise EngineeringError(f"test evidence {evidence_id} must be a successful verification command")
                command = _text(raw.get("command"), "test command", 500)
                parsed.append({"requirement_id": requirement_id, "command": command, "evidence_id": evidence_id})
        key = "implementation_links" if implementation else "test_links"
        existing = {
            (item["requirement_id"], item.get("path") or item.get("command")): item
            for item in self._state[key]
        }
        for item in parsed:
            existing[(item["requirement_id"], item.get("path") or item.get("command"))] = item
        self._state[key] = list(existing.values())

    def _advance_phase(self, arguments: dict[str, Any]) -> None:
        target = str(arguments.get("target_phase", ""))
        if target not in PHASES:
            raise EngineeringError("target_phase is required")
        current_index = PHASES.index(self._state["phase"])
        target_index = PHASES.index(target)
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
            mapped = {item["requirement_id"] for item in self._state["test_links"]}
            uncovered = known_ids - mapped
            if uncovered:
                missing.append("测试证据覆盖需求：" + ", ".join(sorted(uncovered)))
        elif phase == "acceptance":
            verification = self._gate("verification")
            missing.extend(verification["missing"])
            if not _is_approved(decisions.get("project_acceptance")):
                missing.append("用户完成项目验收")
        return {"passed": not missing, "missing": missing}

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

    def _persist(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
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
            commands = [item["command"] for item in self._state["test_links"] if req_id == item["requirement_id"]]
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
        "design_modules": [],
        "implementation_links": [],
        "test_links": [],
        "decisions": [],
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
