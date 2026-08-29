from __future__ import annotations

import json
from typing import Any


def message_size(message: dict[str, Any]) -> int:
    return len(json.dumps(message, ensure_ascii=False, separators=(",", ":")))


class ContextManager:
    """Keeps protocol-valid recent turns under a character budget."""

    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        self.compactions = 0

    def compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if sum(map(message_size, messages)) <= self.max_chars or len(messages) <= 3:
            return messages

        fixed = messages[:2]
        blocks = _conversation_blocks(messages[2:])
        kept: list[list[dict[str, Any]]] = []
        used = sum(map(message_size, fixed))
        reserve = 600
        for block in reversed(blocks):
            block_size = sum(map(message_size, block))
            if kept and used + block_size + reserve > self.max_chars:
                break
            kept.append(block)
            used += block_size
        kept.reverse()
        removed = len(blocks) - len(kept)
        if removed <= 0:
            return messages
        self.compactions += 1
        notice = {
            "role": "system",
            "content": (
                f"Context manager removed {removed} older interaction block(s). "
                "The original task remains above. Re-read files when exact prior output is needed."
            ),
        }
        return fixed + [notice] + [message for block in kept for message in block]


def _conversation_blocks(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    blocks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            if current:
                blocks.append(current)
            current = [message]
        elif current:
            current.append(message)
        else:
            blocks.append([message])
    if current:
        blocks.append(current)
    return blocks

