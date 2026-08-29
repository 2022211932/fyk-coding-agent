from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings


class ModelError(RuntimeError):
    pass


@dataclass
class AssistantReply:
    content: str
    tool_calls: list[dict[str, Any]]
    raw_message: dict[str, Any]


class OpenAICompatibleClient:
    """Minimal Chat Completions client; intentionally not an agent SDK."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantReply:
        body = {
            "model": self.settings.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "thinking": {"type": "enabled"},
            "reasoning_effort": self.settings.reasoning_effort,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.settings.base_url}/chat/completions",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
        )

        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                with urlopen(request, timeout=self.settings.request_timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return self._parse(payload)
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:2000]
                last_error = ModelError(f"Model API returned HTTP {exc.code}: {detail}")
                if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError) as exc:
                last_error = ModelError(f"Model request failed: {exc}")
            if attempt < self.settings.max_retries:
                time.sleep(min(2**attempt, 8))
        raise last_error or ModelError("Model request failed")

    @staticmethod
    def _parse(payload: dict[str, Any]) -> AssistantReply:
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(f"Unexpected model response: {payload!r}") from exc
        content = message.get("content") or ""
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ModelError("tool_calls must be a list")
        return AssistantReply(content=content, tool_calls=calls, raw_message=message)
