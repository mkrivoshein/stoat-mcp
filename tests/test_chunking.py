import time

import pytest

from stoat_mcp.chunking import iter_chunks, split_message


def test_short_message_is_single_chunk():
    assert split_message("hello", 2000) == ["hello"]


def test_empty_message_has_no_chunks():
    assert split_message("", 2000) == []


def test_chunks_respect_limit_and_preserve_text():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(40))
    chunks = split_message(text, 500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks) == text


def test_prefers_line_breaks():
    text = ("a" * 300 + "\n") * 5
    chunks = split_message(text, 700)
    assert all(c.endswith("\n") for c in chunks)


def test_hard_split_without_whitespace():
    text = "x" * 1000
    chunks = split_message(text, 300)
    assert all(len(c) <= 300 for c in chunks)
    assert "".join(chunks) == text


def test_code_fence_is_closed_and_reopened():
    code = "\n".join(f"print({i})" for i in range(200))
    text = f"Intro\n```python\n{code}\n```\nOutro"
    chunks = split_message(text, 400)
    assert len(chunks) > 2
    assert all(len(c) <= 400 for c in chunks)
    for chunk in chunks:
        fences = [l for l in chunk.splitlines() if l.strip().startswith("```")]
        assert len(fences) % 2 == 0, chunk
    assert chunks[1].startswith("```python\n")


def test_limit_too_small():
    with pytest.raises(ValueError):
        split_message("abc", 10)


def test_overlong_fence_line_terminates():
    """Regression: a fence line that leaves no room to reopen it used to loop forever."""
    text = "```" + "a" * 1992 + "\n" + "x" * 100
    chunks = split_message(text, 2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert "x" * 100 in "".join(chunks)


@pytest.mark.parametrize("limit", [32, 33, 50, 2000])
def test_always_makes_progress(limit):
    text = "```" + "b" * (limit * 3) + "\n" + "```py\n" + "c " * limit
    chunks = split_message(text, limit)
    assert chunks
    assert all(len(c) <= limit for c in chunks)


def test_max_chunks_stops_early_on_huge_input():
    text = "z" * (20 * 1024 * 1024)
    start = time.monotonic()
    chunks = split_message(text, 2000, max_chunks=8)
    assert len(chunks) == 9
    assert time.monotonic() - start < 1


def test_iter_chunks_is_lazy():
    gen = iter_chunks("q" * 10_000, 100)
    assert next(gen) == "q" * 96
