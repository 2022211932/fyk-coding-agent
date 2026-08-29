from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, workspace: Path):
        self.path = workspace / ".fyk-agent" / "events.jsonl"

    def emit(self, kind: str, **data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **data}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
