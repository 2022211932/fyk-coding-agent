from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import uuid


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    relative_path: str
    existed: bool
    content: bytes


class ChangeJournal:
    """Append-only local snapshots used to undo agent file writes."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.state_dir = self.workspace / ".yukai"
        self.snapshot_file = self.state_dir / "snapshots.jsonl"

    def capture(self, path: Path) -> str:
        resolved = path.resolve()
        snapshot_id = uuid.uuid4().hex[:12]
        record = {
            "id": snapshot_id,
            "time": datetime.now(timezone.utc).isoformat(),
            "path": resolved.relative_to(self.workspace).as_posix(),
            "existed": resolved.is_file(),
            "content_b64": base64.b64encode(resolved.read_bytes()).decode("ascii")
            if resolved.is_file()
            else "",
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.snapshot_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return snapshot_id

    def undo_last(self) -> Snapshot | None:
        if not self.snapshot_file.is_file():
            return None
        lines = self.snapshot_file.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        record = json.loads(lines[-1])
        target = (self.workspace / record["path"]).resolve()
        target.relative_to(self.workspace)
        content = base64.b64decode(record["content_b64"])
        if record["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        elif target.exists():
            target.unlink()
        remaining = "\n".join(lines[:-1])
        self.snapshot_file.write_text(remaining + ("\n" if remaining else ""), encoding="utf-8")
        return Snapshot(record["id"], record["path"], record["existed"], content)

    def unified_diff(self, snapshot_id: str, relative_path: str) -> dict[str, object]:
        """Compare a captured pre-edit snapshot with the file currently on disk."""
        record = self._find_snapshot(snapshot_id)
        if record is None:
            raise ValueError("Snapshot not found")
        recorded_path = str(record.get("path", ""))
        if recorded_path != Path(relative_path).as_posix():
            raise ValueError("Snapshot does not belong to the requested file")
        target = (self.workspace / recorded_path).resolve()
        target.relative_to(self.workspace)
        before = base64.b64decode(str(record.get("content_b64", ""))).decode(
            "utf-8", errors="replace"
        )
        after = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{recorded_path}",
                tofile=f"b/{recorded_path}",
            )
        )
        max_chars = 200_000
        truncated = len(diff) > max_chars
        return {
            "ok": True,
            "path": recorded_path,
            "snapshot_id": snapshot_id,
            "diff": diff[:max_chars],
            "truncated": truncated,
        }

    def _find_snapshot(self, snapshot_id: str) -> dict[str, object] | None:
        if not self.snapshot_file.is_file():
            return None
        for line in reversed(self.snapshot_file.read_text(encoding="utf-8").splitlines()):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("id") == snapshot_id:
                return record
        return None
