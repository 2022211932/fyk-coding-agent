from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from . import __version__
from .agent import CodingAgent, RunResult
from .client import ModelError, OpenAICompatibleClient
from .config import Settings
from .engineering import EngineeringWorkflow
from .tools import ToolRegistry
from .ui import TerminalUI
from .workspace import Workspace, WorkspaceError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yukai",
        description="Yukai - a lightweight autonomous coding agent powered by DeepSeek",
    )
    parser.add_argument("task", nargs="*", help="Programming task; omit to open the coding shell")
    parser.add_argument(
        "-w",
        "--workspace",
        help="Workspace directory (web mode remembers the last selected project)",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-approve safe changes; high-risk commands still ask")
    parser.add_argument("--model", help="Override DEEPSEEK_MODEL for this session")
    parser.add_argument("--max-steps", type=int, help="Maximum model steps per user prompt")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI terminal colors")
    parser.add_argument("--web", action="store_true", help="Open the local visual Agent console")
    parser.add_argument(
        "--engineering",
        action="store_true",
        help="Use the evidence-gated software-engineering lifecycle",
    )
    parser.add_argument("--api-port", type=int, default=8765, help=argparse.SUPPRESS)
    parser.add_argument("--frontend-port", type=int, default=3000, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"Yukai {__version__}")
    return parser


class ApprovalController:
    def __init__(self, ui: TerminalUI, approve_all: bool = False):
        self.ui = ui
        self.approve_all = approve_all

    def __call__(self, name: str, arguments: dict[str, Any]) -> bool:
        if self.approve_all:
            return True
        answer = self.ui.approval(name, _safe_arguments(arguments))
        if answer in {"a", "all"}:
            self.approve_all = True
            self.ui.notice("Automatic approval enabled for the rest of this session.")
            return True
        return answer in {"y", "yes"}

    def require_explicit(self, name: str, arguments: dict[str, Any], reason: str) -> bool:
        answer = self.ui.approval(
            name,
            _safe_arguments(arguments),
            risk_reason=reason,
            allow_all=False,
        )
        return answer in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    _configure_console_encoding()
    args = build_parser().parse_args(argv)
    ui = TerminalUI(color=False if args.no_color else None)
    try:
        workspace_path = args.workspace
        if args.web and workspace_path is None:
            from .web import load_last_workspace

            remembered = load_last_workspace()
            workspace_path = str(remembered) if remembered else "."
        workspace = Workspace(Path(workspace_path or "."))
        settings = Settings.from_environment(workspace.root)
        if args.model:
            settings = _replace_setting(settings, model=args.model)
        if args.max_steps is not None:
            if args.max_steps <= 0:
                raise ValueError("--max-steps must be positive")
            settings = _replace_setting(settings, max_steps=args.max_steps)
    except (ValueError, WorkspaceError) as exc:
        ui.error(f"Configuration error: {exc}")
        return 2

    approvals = ApprovalController(ui, approve_all=args.yes)
    registry = ToolRegistry(workspace, approve=approvals, approve_risky=approvals.require_explicit)
    engineering = EngineeringWorkflow(workspace.root) if args.engineering else None
    agent = CodingAgent(
        OpenAICompatibleClient(settings),
        registry,
        max_steps=settings.max_steps,
        max_context_chars=settings.max_context_chars,
        notify=lambda kind, data: _terminal_notification(ui, kind, data),
        engineering=engineering,
    )
    if args.web:
        if args.task:
            ui.error("Web mode does not accept a one-shot task; enter it in the browser.")
            return 2
        from .web import run_web_console

        try:
            return run_web_console(
                settings,
                workspace,
                automatic_approval=args.yes,
                api_port=args.api_port,
                frontend_port=args.frontend_port,
            )
        except (OSError, RuntimeError) as exc:
            ui.error(f"Web console error: {exc}")
            return 1
    ui.banner(
        version=__version__,
        model=settings.model,
        workspace=workspace.root,
        automatic_approval=approvals.approve_all,
    )

    task = " ".join(args.task).strip()
    try:
        if task:
            result = _run_task(agent, task, ui, engineering=engineering)
            return 0 if result.stop_reason == "completed" else 3
        return _interactive_loop(agent, registry, approvals, settings, ui, engineering=engineering)
    except KeyboardInterrupt:
        ui.error("\nCancelled by user.")
        return 130
    except ModelError as exc:
        ui.error(f"Model error: {exc}")
        return 1


def _interactive_loop(
    agent: CodingAgent,
    registry: ToolRegistry,
    approvals: ApprovalController,
    settings: Settings,
    ui: TerminalUI,
    engineering: EngineeringWorkflow | None = None,
) -> int:
    history: list[dict[str, Any]] | None = None
    while True:
        try:
            task = ui.prompt()
        except EOFError:
            ui.write()
            return 0
        if not task:
            continue
        command = task.lower()
        if command in {"/exit", "/quit", ":quit", ":q"}:
            return 0
        if command in {"/help", ":help"}:
            ui.help()
            continue
        if command in {"/clear", ":clear"}:
            history = None
            agent.clear_context()
            ui.notice("Conversation cleared. Workspace files were not changed.")
            continue
        if command in {"/undo", ":undo"}:
            undo_result = registry.undo_last()
            if undo_result.get("ok"):
                ui.notice(f"Restored {undo_result['path']}")
            else:
                ui.error(str(undo_result.get("error", "Nothing to undo")))
            continue
        if command in {"/status", ":status"}:
            ui.status(
                model=settings.model,
                workspace=registry.workspace.root,
                automatic_approval=approvals.approve_all,
                history=history,
            )
            continue
        if command in {"/history", ":history"}:
            ui.history(history)
            continue
        if task.startswith("/") or task.startswith(":"):
            ui.error(f"Unknown command: {task}. Use /help to list commands.")
            continue
        try:
            result = _run_task(agent, task, ui, history=history, engineering=engineering)
            history = result.messages
        except ModelError as exc:
            ui.error(f"Model error: {exc}")


def _run_task(
    agent: CodingAgent,
    task: str,
    ui: TerminalUI,
    *,
    history: list[dict[str, Any]] | None = None,
    engineering: EngineeringWorkflow | None = None,
) -> RunResult:
    result = agent.run(task, history=history)
    while result.stop_reason == "awaiting_user" and engineering is not None:
        question = engineering.payload().get("pending_question")
        if not isinstance(question, dict):
            break
        option_id, answer = ui.engineering_question(question)
        engineering.answer_question(
            str(question["question_id"]), option_id=option_id, answer=answer
        )
        follow_up = f"用户对工程决策“{question['question']}”的回答是“{answer}”。请继续执行。"
        result = agent.run(follow_up, history=result.messages)
    ui.answer(
        result.final_text,
        steps=result.steps,
        stop_reason=result.stop_reason,
        compactions=result.context_compactions,
    )
    return result


def _terminal_notification(ui: TerminalUI, kind: str, data: dict[str, Any]) -> None:
    if kind == "model_request":
        ui.model_request(data["step"])
    elif kind == "tool_call":
        ui.tool_call(data["tool"], data.get("arguments", {}), data.get("error"))
    elif kind == "tool_result":
        ui.tool_result(data["tool"], data.get("result", {}))


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in {"content", "old_text", "new_text"} and isinstance(value, str):
            safe[key] = f"<{len(value)} characters>"
        else:
            safe[key] = value
    return safe


def _safe_argument_summary(arguments: dict[str, Any]) -> str:
    """Backward-compatible helper used by tests and external callers."""
    return json.dumps(_safe_arguments(arguments), ensure_ascii=False, indent=2, default=str)[:4000]


def _replace_setting(settings: Settings, **changes: Any) -> Settings:
    values = {
        "api_key": settings.api_key,
        "base_url": settings.base_url,
        "model": settings.model,
        "max_steps": settings.max_steps,
        "max_context_chars": settings.max_context_chars,
        "request_timeout": settings.request_timeout,
        "max_retries": settings.max_retries,
        "reasoning_effort": settings.reasoning_effort,
    }
    values.update(changes)
    return Settings(**values)


def _configure_console_encoding() -> None:
    if os.name != "nt":
        return
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
