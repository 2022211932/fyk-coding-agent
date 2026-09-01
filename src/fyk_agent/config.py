from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Load a tiny, predictable subset of .env syntax without dependencies."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    max_steps: int = 30
    engineering_max_steps: int = 60
    max_context_chars: int = 120_000
    request_timeout: int = 120
    max_retries: int = 3
    reasoning_effort: str = "high"

    @classmethod
    def from_environment(cls, workspace: Path) -> "Settings":
        load_dotenv(workspace / ".env")
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is missing. Set it in the environment or an untracked .env file."
            )
        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
            max_steps=_positive_int("YUKAI_MAX_STEPS", 30, "FYK_AGENT_MAX_STEPS"),
            engineering_max_steps=_positive_int("YUKAI_ENGINEERING_MAX_STEPS", 60),
            max_context_chars=_positive_int(
                "YUKAI_MAX_CONTEXT_CHARS", 800_000, "FYK_AGENT_MAX_CONTEXT_CHARS"
            ),
            request_timeout=_positive_int(
                "YUKAI_REQUEST_TIMEOUT", 120, "FYK_AGENT_REQUEST_TIMEOUT"
            ),
            max_retries=_positive_int("YUKAI_MAX_RETRIES", 3, "FYK_AGENT_MAX_RETRIES"),
            reasoning_effort=_choice(
                "DEEPSEEK_REASONING_EFFORT", "high", {"low", "high", "max"}
            ),
        )


def _positive_int(name: str, default: int, legacy_name: str | None = None) -> int:
    raw = os.getenv(name)
    if raw is None and legacy_name:
        raw = os.getenv(legacy_name)
    if raw is None:
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _choice(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in choices:
        options = ", ".join(sorted(choices))
        raise ValueError(f"{name} must be one of: {options}")
    return value
