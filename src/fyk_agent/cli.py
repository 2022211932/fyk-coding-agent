from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from . import __version__
from .agent import CodingAgent
from .client import ModelError, OpenAICompatibleClient
from .config import Settings
from .tools import ToolRegistry
from .workspace import Workspace, WorkspaceError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fyk-agent",
        description="FYK Coding Agent - a local, auditable coding agent powered by DeepSeek",
    )
    parser.add_argument("task", nargs="*", help="Programming task; omit to enter interactive mode")
    parser.add_argument("-w", "--workspace", default=".", help="Workspace directory (default: .)")
    parser.add_argument("-y", "--yes", action="store_true", help="Approve all state-changing tools")
    parser.add_argument("--model", help="Override DEEPSEEK_MODEL for this run")
    parser.add_argument("--max-steps", type=int, help="Override the maximum number of model steps")
    parser.add_argument("--version", action="version", version=f"FYK Coding Agent {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console_encoding()
    args = build_parser().parse_args(argv)
    try:
        workspace = Workspace(Path(args.workspace))
        settings = Settings.from_environment(workspace.root)
        if args.model:
            settings = _replace_setting(settings, model=args.model)
        if args.max_steps is not None:
            if args.max_steps <= 0:
                raise ValueError("--max-steps must be positive")
            settings = _replace_setting(settings, max_steps=args.max_steps)
    except (ValueError, WorkspaceError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    approve = _always_approve if args.yes else _interactive_approval
    registry = ToolRegistry(workspace, approve=approve)
    agent = CodingAgent(
        OpenAICompatibleClient(settings),
        registry,
        max_steps=settings.max_steps,
        max_context_chars=settings.max_context_chars,
        notify=_terminal_notification,
    )
    print(
        f"FYK Coding Agent | model={settings.model} | workspace={workspace.root}\n"
        f"Approval mode: {'automatic' if args.yes else 'interactive'}"
    )

    tasks = [" ".join(args.task)] if args.task else None
    try:
        if tasks:
            return _run_task(agent, tasks[0])
        return _interactive_loop(agent, registry)
    except KeyboardInterrupt:
        print("\nCancelled by user.", file=sys.stderr)
        return 130
    except ModelError as exc:
        print(f"Model error: {exc}", file=sys.stderr)
        return 1


def _interactive_loop(agent: CodingAgent, registry: ToolRegistry) -> int:
    print("Enter a task, :undo to revert the last file write, :help for commands, or :quit.")
    while True:
        try:
            task = input("\nfyk> ").strip()
        except EOFError:
            print()
            return 0
        if not task:
            continue
        if task in {":quit", ":q", "quit", "exit"}:
            return 0
        if task == ":help":
            print(":undo  restore the file state before the latest write/edit")
            print(":quit  exit FYK Coding Agent")
            continue
        if task == ":undo":
            print(json.dumps(registry.undo_last(), ensure_ascii=False, indent=2))
            continue
        _run_task(agent, task)


def _run_task(agent: CodingAgent, task: str) -> int:
    result = agent.run(task)
    print("\nAgent result:\n" + result.final_text)
    print(
        f"\n[steps={result.steps}, stop={result.stop_reason}, "
        f"context_compactions={result.context_compactions}]"
    )
    return 0 if result.stop_reason == "completed" else 3


def _always_approve(_name: str, _arguments: dict[str, Any]) -> bool:
    return True


def _interactive_approval(name: str, arguments: dict[str, Any]) -> bool:
    summary = _safe_argument_summary(arguments)
    print(f"\nApproval required: {name}\n{summary}")
    answer = input("Execute? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _safe_argument_summary(arguments: dict[str, Any]) -> str:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in {"content", "old_text", "new_text"} and isinstance(value, str):
            safe[key] = f"<{len(value)} characters>"
        else:
            safe[key] = value
    return json.dumps(safe, ensure_ascii=False, indent=2, default=str)[:4000]


def _terminal_notification(kind: str, data: dict[str, Any]) -> None:
    if kind == "model_request":
        print(f"\n[step {data['step']}] Asking model...", flush=True)
    elif kind == "tool_call":
        print(f"  -> {data['tool']}", flush=True)
    elif kind == "tool_result":
        status = "ok" if data["ok"] else "failed"
        print(f"  <- {data['tool']}: {status}", flush=True)


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
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
