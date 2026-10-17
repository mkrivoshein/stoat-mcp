"""Split long message content into chunks that fit Stoat's length limit."""

from __future__ import annotations

FENCE = "```"


def _cut_point(text: str, limit: int) -> int:
    """Choose where to cut ``text`` so the head is at most ``limit`` chars.

    Prefers a paragraph break, then a line break, then a space; falls back to
    a hard cut. Only accepts a soft break in the back half of the window so
    chunks don't become tiny.
    """
    if len(text) <= limit:
        return len(text)
    window = text[:limit]
    floor = limit // 2
    for sep in ("\n\n", "\n", " "):
        idx = window.rfind(sep)
        if idx >= floor:
            return idx + len(sep)
    return limit


def split_message(content: str, limit: int = 2000) -> list[str]:
    """Split ``content`` into chunks of at most ``limit`` characters.

    Fenced code blocks that straddle a boundary are closed at the end of one
    chunk and reopened (with the same info string) at the start of the next,
    so each chunk renders correctly on its own.
    """
    if limit < 32:
        raise ValueError("limit must be at least 32 characters")
    if not content:
        return []

    chunks: list[str] = []
    remaining = content
    open_fence: str | None = None  # the opening fence line, e.g. "```python"

    while remaining:
        prefix = f"{open_fence}\n" if open_fence else ""
        # Reserve room for a closing fence we might need to append.
        budget = limit - len(prefix) - (len(FENCE) + 1)
        cut = _cut_point(remaining, budget) if len(prefix) + len(remaining) > limit else len(remaining)
        body = remaining[:cut]
        remaining = remaining[cut:]

        fence_state = open_fence
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith(FENCE):
                fence_state = None if fence_state else stripped

        chunk = prefix + body
        if fence_state and remaining:
            chunk = chunk.rstrip("\n") + "\n" + FENCE
        chunks.append(chunk)
        open_fence = fence_state if remaining else None

    return chunks
