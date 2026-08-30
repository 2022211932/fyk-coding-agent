from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, TextIO


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"


class TerminalUI:
    """Small dependency-free terminal renderer for the interactive coding shell."""

    def __init__(
        self,
        *,
        color: bool | None = None,
        stream: TextIO | None = None,
        error_stream: TextIO | None = None,
    ):
        self.stream = stream or sys.stdout
        self.error_stream = error_stream or sys.stderr
        if color is None:
            color = bool(getattr(self.stream, "isatty", lambda: False)()) and "NO_COLOR" not in os.environ
        self.color = color

    def paint(self, text: str, style: str) -> str:
        return f"{style}{text}{RESET}" if self.color else text

    def banner(
        self,
        *,
        version: str,
        model: str,
        workspace: Path,
        automatic_approval: bool,
    ) -> None:
        width = 72
        title = f" Yukai {version} "
        top = "╭─" + title + "─" * max(0, width - len(title) - 2) + "╮"
        approval = "auto-approve safe changes; ask for high-risk commands" if automatic_approval else "ask before changes"
        self.write(self.paint(top, CYAN))
        self.write(f"│  Model      {self.paint(model, BOLD)}")
        self.write(f"│  Workspace  {workspace}")
        self.write(f"│  Safety     {approval}")
        self.write("│")
        self.write(f"│  {self.paint('/help', CYAN)} for commands · {self.paint('/exit', CYAN)} to quit")
        self.write(self.paint("╰" + "─" * (width - 1) + "╯", CYAN))

    def prompt(self) -> str:
        first = input(self.paint("\n❯ ", BOLD + CYAN))
        lines = []
        while first.endswith("\\"):
            lines.append(first[:-1])
            first = input(self.paint("  … ", DIM))
        lines.append(first)
        return "\n".join(lines).strip()

    def model_request(self, step: int) -> None:
        self.write(self.paint(f"\n● Thinking…  step {step}", DIM))

    def tool_call(self, name: str, arguments: dict[str, Any], error: str | None = None) -> None:
        label = _tool_label(name, arguments)
        if error:
            label += f" ({error})"
        self.write(f"  {self.paint('›', BLUE)} {label}")

    def tool_result(self, name: str, result: dict[str, Any]) -> None:
        ok = bool(result.get("ok"))
        symbol = self.paint("✓", GREEN) if ok else self.paint("✗", RED)
        detail = _result_detail(name, result)
        duration = result.get("duration_ms")
        suffix = f" · {duration:.0f}ms" if isinstance(duration, (int, float)) else ""
        self.write(f"    {symbol} {detail}{self.paint(suffix, DIM)}")

    def answer(self, text: str, *, steps: int, stop_reason: str, compactions: int) -> None:
        self.write("\n" + self.paint("● Answer", GREEN if stop_reason == "completed" else YELLOW))
        self.write(text)
        meta = f"{steps} model step(s) · {stop_reason} · {compactions} context compaction(s)"
        self.write(self.paint(meta, DIM))

    def approval(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        risk_reason: str = "",
        allow_all: bool = True,
    ) -> str:
        title = "High-risk operation requires confirmation" if risk_reason else "Permission required"
        self.write("\n" + self.paint(title, YELLOW + BOLD))
        self.write(f"  {_tool_label(name, arguments)}")
        if risk_reason:
            self.write(self.paint(f"  Risk: {risk_reason}", RED))
        choices = "  y: allow once  n: reject"
        if allow_all:
            choices += "  a: allow all safe changes for this session"
        self.write(self.paint(choices, DIM))
        try:
            return input(self.paint("  Allow? [y/N/a] ", YELLOW)).strip().lower()
        except EOFError:
            return "n"

    def help(self) -> None:
        self.write(
            """
/help       Show this command list
/status     Show model, workspace, approval mode, and conversation size
/history    Show user prompts in the current conversation
/clear      Start a fresh conversation (files are unchanged)
/undo       Restore the file before the latest write/edit
/exit       Exit Yukai

End a line with \\ to continue entering a multi-line prompt.
Plain text is sent to the agent as the next instruction.
""".strip()
        )

    def status(
        self,
        *,
        model: str,
        workspace: Path,
        automatic_approval: bool,
        history: list[dict[str, Any]] | None,
    ) -> None:
        user_turns = sum(message.get("role") == "user" for message in history or [])
        message_count = len(history or [])
        self.write(f"Model:       {model}")
        self.write(f"Workspace:   {workspace}")
        self.write(f"Approval:    {'automatic' if automatic_approval else 'interactive'}")
        self.write(f"Conversation:{user_turns} user turn(s), {message_count} message(s)")

    def history(self, messages: list[dict[str, Any]] | None) -> None:
        prompts = [
            str(message.get("content", ""))
            for message in messages or []
            if message.get("role") == "user"
        ]
        if not prompts:
            self.write("No prompts in the current conversation.")
            return
        for index, prompt in enumerate(prompts, 1):
            self.write(f"{index:>2}. {_one_line(prompt, 140)}")

    def notice(self, text: str) -> None:
        self.write(self.paint(text, CYAN))

    def error(self, text: str) -> None:
        print(self.paint(text, RED), file=self.error_stream, flush=True)

    def write(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)


def _tool_label(name: str, arguments: dict[str, Any]) -> str:
    path = str(arguments.get("path", "."))
    labels = {
        "list_files": f"List {path}",
        "read_file": f"Read {path}",
        "search_text": f"Search {arguments.get('query', '')!r} in {path}",
        "write_file": f"Write {path}",
        "edit_file": f"Edit {path}",
        "make_directory": f"Mkdir {path}",
        "run_command": f"Bash {_one_line(str(arguments.get('command', '')), 110)}",
        "update_plan": f"Plan {str(arguments.get('summary', 'task'))[:90]}",
    }
    return labels.get(name, f"{name} {_one_line(json.dumps(arguments, ensure_ascii=False), 100)}")


def _result_detail(name: str, result: dict[str, Any]) -> str:
    if result.get("ok"):
        if name == "list_files":
            return f"{len(result.get('entries', []))} entries"
        if name == "search_text":
            return f"{len(result.get('matches', []))} matches"
        if name == "read_file":
            return f"{result.get('total_lines', 0)} lines"
        if name in {"write_file", "edit_file", "make_directory"}:
            return str(result.get("path", "done"))
        if name == "run_command":
            return f"exit {result.get('exit_code', 0)}"
        if name == "update_plan":
            plan = result.get("plan", {})
            return f"{plan.get('completed', 0)}/{plan.get('total', 0)} steps completed"
        return "done"
    error = str(result.get("error") or result.get("error_type") or "failed")
    return _one_line(error, 140)


def _one_line(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"
