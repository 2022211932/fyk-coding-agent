import re


def slugify(text: str) -> str:
    """Convert a title to a URL-safe ASCII slug."""
    return re.sub(r"\s+", "-", text.strip().lower())

