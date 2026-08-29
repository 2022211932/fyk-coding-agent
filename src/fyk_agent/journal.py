from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
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
        self.state_dir = self.workspace / ".fyk-agent"
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
