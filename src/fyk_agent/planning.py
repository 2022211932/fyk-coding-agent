from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


PLAN_STATUSES = {"pending", "in_progress", "completed", "blocked"}
PLAN_KINDS = {"inspect", "change", "verify", "other"}
KIND_TOOLS = {
    "inspect": {"list_files", "read_file", "search_text"},
    "change": {"write_file", "edit_file", "make_directory"},
    "verify": {"run_command"},
}


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    tool: str
    ok: bool
    summary: str
    step: int
    verification: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "tool": self.tool,
            "ok": self.ok,
            "summary": self.summary,
            "step": self.step,
            "verification": self.verification,
        }


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    title: str
    kind: str
    status: str
    evidence_ids: tuple[str, ...] = ()
    note: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "title": self.title,
            "kind": self.kind,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "note": self.note,
        }


@dataclass
class TaskPlan:
    summary: str = ""
    steps: list[PlanStep] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        return bool(self.steps)

    @property
    def terminal(self) -> bool:
        return self.exists and all(step.status in {"completed", "blocked"} for step in self.steps)

    @property
    def blocked(self) -> bool:
        return any(step.status == "blocked" for step in self.steps)

    @property
    def completed_count(self) -> int:
        return sum(step.status == "completed" for step in self.steps)

    def unfinished_titles(self) -> list[str]:
        return [
            step.title
            for step in self.steps
            if step.status in {"pending", "in_progress"}
        ]


class PlanTracker:
    """Validated, evidence-bound task plan scoped to one agent run."""

    schema = {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": (
                "Create or update the user-visible task plan for a multi-step task. "
                "A completed step must cite compatible evidence IDs returned by successful tools. "
                "Use inspect for repository investigation, change for file changes, verify for "
                "tests/builds, and other only when none of those categories fit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 200},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 7,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "minLength": 1, "maxLength": 40},
                                "title": {"type": "string", "minLength": 1, "maxLength": 160},
                                "kind": {"type": "string", "enum": sorted(PLAN_KINDS)},
                                "status": {"type": "string", "enum": sorted(PLAN_STATUSES)},
                                "evidence_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 20,
                                },
                                "note": {"type": "string", "maxLength": 300},
                            },
                            "required": ["id", "title", "kind", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "steps"],
                "additionalProperties": False,
            },
        },
    }

    def __init__(self) -> None:
        self.plan = TaskPlan()
        self.evidence: dict[str, Evidence] = {}
        self._evidence_counter = 0

    def reset(self) -> None:
        self.plan = TaskPlan()
        self.evidence = {}
        self._evidence_counter = 0

    def register_evidence(
        self,
        tool: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        *,
        step: int,
        evidence_id: str | None = None,
    ) -> str:
        self._evidence_counter += 1
        if evidence_id:
            normalized = re.sub(r"[^A-Za-z0-9_-]", "-", evidence_id)[:70]
            candidate = f"evidence-{normalized}"
        else:
            candidate = f"evidence-{self._evidence_counter}"
        evidence_id = candidate
        while evidence_id in self.evidence:
            evidence_id = f"{candidate}-{self._evidence_counter}"
        self.evidence[evidence_id] = Evidence(
            evidence_id=evidence_id,
            tool=tool,
            ok=bool(result.get("ok")),
            summary=_evidence_summary(tool, arguments, result),
            step=step,
            verification=tool == "run_command"
            and _is_verification_command(str(arguments.get("command", ""))),
        )
        return evidence_id

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            summary = _required_text(arguments.get("summary"), "summary", 200)
            raw_steps = arguments.get("steps")
            if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 7:
                raise PlanError("steps must contain between 1 and 7 items")
            previous = {step.step_id: step for step in self.plan.steps}
            steps = [self._parse_step(raw, previous) for raw in raw_steps]
            ids = [step.step_id for step in steps]
            if len(ids) != len(set(ids)):
                raise PlanError("plan step IDs must be unique")
            if sum(step.status == "in_progress" for step in steps) > 1:
                raise PlanError("only one plan step can be in_progress")
            for old_step in self.plan.steps:
                if old_step.status == "completed":
                    replacement = next((step for step in steps if step.step_id == old_step.step_id), None)
                    if replacement is None or replacement.status != "completed":
                        raise PlanError("completed plan steps cannot be removed or reopened")
                    if (replacement.title, replacement.kind) != (old_step.title, old_step.kind):
                        raise PlanError("completed plan steps cannot change title or kind")
                    if not set(old_step.evidence_ids).issubset(replacement.evidence_ids):
                        raise PlanError("evidence cannot be removed from a completed plan step")
            self.plan = TaskPlan(summary=summary, steps=steps)
            return {"ok": True, "plan": self.payload()}
        except PlanError as exc:
            return {"ok": False, "error": str(exc), "error_type": "invalid_plan"}

    def payload(self) -> dict[str, Any]:
        referenced = {
            evidence_id
            for step in self.plan.steps
            for evidence_id in step.evidence_ids
        }
        return {
            "summary": self.plan.summary,
            "steps": [step.payload() for step in self.plan.steps],
            "completed": self.plan.completed_count,
            "total": len(self.plan.steps),
            "terminal": self.plan.terminal,
            "blocked": self.plan.blocked,
            "evidence": [
                evidence.payload()
                for evidence_id, evidence in self.evidence.items()
                if evidence_id in referenced
            ],
        }

    def _parse_step(
        self, raw: Any, previous: dict[str, PlanStep]
    ) -> PlanStep:
        if not isinstance(raw, dict):
            raise PlanError("each plan step must be an object")
        step_id = _required_text(raw.get("id"), "step id", 40)
        title = _required_text(raw.get("title"), f"title for {step_id}", 160)
        kind = str(raw.get("kind", ""))
        status = str(raw.get("status", ""))
        if kind not in PLAN_KINDS:
            raise PlanError(f"invalid kind for {step_id}: {kind}")
        if status not in PLAN_STATUSES:
            raise PlanError(f"invalid status for {step_id}: {status}")
        raw_evidence = raw.get("evidence_ids", [])
        if not isinstance(raw_evidence, list) or not all(
            isinstance(item, str) for item in raw_evidence
        ):
            raise PlanError(f"evidence_ids for {step_id} must be a string array")
        evidence_ids = tuple(dict.fromkeys(raw_evidence))
        old_step = previous.get(step_id)
        if status == "completed" and not evidence_ids and old_step is not None:
            evidence_ids = old_step.evidence_ids
        note = str(raw.get("note", "")).strip()[:300]
        if status == "blocked" and not note:
            raise PlanError(f"blocked step {step_id} requires a note")
        if status == "completed":
            if not evidence_ids:
                raise PlanError(f"completed step {step_id} requires evidence_ids")
            self._validate_evidence(step_id, kind, evidence_ids)
        return PlanStep(step_id, title, kind, status, evidence_ids, note)

    def _validate_evidence(
        self, step_id: str, kind: str, evidence_ids: tuple[str, ...]
    ) -> None:
        records: list[Evidence] = []
        for evidence_id in evidence_ids:
            evidence = self.evidence.get(evidence_id)
            if evidence is None:
                raise PlanError(f"unknown evidence ID for {step_id}: {evidence_id}")
            records.append(evidence)
        compatible_tools = KIND_TOOLS.get(kind)
        compatible = [
            record
            for record in records
            if record.ok
            and (compatible_tools is None or record.tool in compatible_tools)
            and (kind != "verify" or record.verification)
        ]
        if not compatible:
            expected = ", ".join(sorted(compatible_tools or {"a successful tool"}))
            raise PlanError(
                f"completed {kind} step {step_id} requires successful evidence from: {expected}"
            )


def _required_text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{label} must be a non-empty string")
    text = " ".join(value.split())
    if len(text) > limit:
        raise PlanError(f"{label} exceeds {limit} characters")
    return text


def _evidence_summary(
    tool: str, arguments: dict[str, Any], result: dict[str, Any]
) -> str:
    path = result.get("path") or arguments.get("path")
    if tool == "run_command":
        command = " ".join(str(arguments.get("command", "")).split())[:100]
        return f"{command} · exit {result.get('exit_code')}"
    if path:
        return str(path)[:160]
    if tool == "search_text":
        return f"search: {str(arguments.get('query', ''))[:120]}"
    return tool


_VERIFICATION_COMMANDS = [
    r"^(?:python|py)(?:\.exe)?\s+-m\s+(?:pytest|unittest|compileall)\b",
    r"^(?:pytest|tox|ruff|mypy|eslint|tsc)\b",
    r"^(?:npm|pnpm|yarn|bun)\s+(?:test|run\s+(?:test|lint|build|check|typecheck))\b",
    r"^(?:go\s+test|cargo\s+(?:test|check)|dotnet\s+(?:test|build))\b",
    r"^(?:make|gradle|gradlew|mvn|mvnw)\b.*\b(?:test|check|build|verify)\b",
]


def _is_verification_command(command: str) -> bool:
    normalized = " ".join(command.strip().split()).lower()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _VERIFICATION_COMMANDS)
