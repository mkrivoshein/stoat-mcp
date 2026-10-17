"""Split long message content into chunks that fit Stoat's length limit."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import islice

FENCE = "```"


def _cut_point(text: str, start: int, budget: int) -> int:
    """Return the end index of a chunk starting at ``start``.

    Prefers a paragraph break, then a line break, then a space; falls back to
    a hard cut. Only accepts a soft break in the back half of the window so
    chunks don't become tiny. Always advances by at least one character.
    """
    end = start + budget
    if end >= len(text):
        return len(text)
    floor = start + budget // 2
    for sep in ("\n\n", "\n", " "):
        idx = text.rfind(sep, start, end)
        if idx >= floor and idx + len(sep) <= end:
            return idx + len(sep)
    return max(end, start + 1)


def iter_chunks(content: str, limit: int = 2000) -> Iterator[str]:
    """Lazily yield chunks of ``content`` of at most ``limit`` characters.

    Fenced code blocks that straddle a boundary are closed at the end of one
    chunk and reopened (with the same info string) at the start of the next,
    so each chunk renders correctly on its own. A fence line too long to
    repeat is not carried over; the text is still split within the limit.
    """
    if limit < 32:
        raise ValueError("limit must be at least 32 characters")

    pos = 0
    open_fence: str | None = None  # the opening fence line, e.g. "```python"
    closing = len(FENCE) + 1  # room for "\n```"

    while pos < len(content):
        prefix = f"{open_fence}\n" if open_fence else ""
        if len(prefix) > limit // 2:
            # Reopening this fence would leave too little room for content.
            prefix, open_fence = "", None

        if len(prefix) + len(content) - pos <= limit:
            end = len(content)
        else:
            end = _cut_point(content, pos, limit - len(prefix) - closing)
        body = content[pos:end]
        pos = end

        fence_state = open_fence
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith(FENCE):
                fence_state = None if fence_state else stripped

        chunk = prefix + body
        more = pos < len(content)
        if fence_state and more:
            chunk = chunk.rstrip("\n") + "\n" + FENCE
        yield chunk
        open_fence = fence_state if more else None


def split_message(content: str, limit: int = 2000, max_chunks: int | None = None) -> list[str]:
    """Split ``content`` into chunks of at most ``limit`` characters.

    With ``max_chunks``, stops after ``max_chunks + 1`` chunks, which is
    enough for a caller to tell that the content is over its threshold.
    """
    chunks = iter_chunks(content, limit)
    if max_chunks is not None:
        return list(islice(chunks, max_chunks + 1))
    return list(chunks)
