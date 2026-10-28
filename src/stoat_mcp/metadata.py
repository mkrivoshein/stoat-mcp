"""Structured metadata headers on messages, e.g. ``[TASK] id:42 | priority:high``.

A header is the first line of a message:

    [TYPE] key:value | key:value | ...

- ``TYPE`` starts with a letter and has up to 32 letters, digits, ``_`` or
  ``-``. It is case-insensitive and normalised to upper case.
- ``key`` starts with a letter and has letters, digits, ``_`` or ``-``. It is
  case-insensitive and normalised to lower case. Fields are optional.
- ``value`` is the text after the first ``:``, trimmed. It may contain ``:``;
  a literal ``|`` or ``\\`` is written as ``\\|`` or ``\\\\``.

Everything after the header line is the message body. Parsing is tolerant:
content whose first line is not a header is plain text, and header segments
that are not ``key:value`` pairs are ignored.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
HEADER_RE = re.compile(r"\[(" + TYPE_RE.pattern + r")\](?:[ \t]+(.*))?")


def _split_fields(text: str) -> list[str]:
    """Split on unescaped ``|``, unescaping ``\\|`` and ``\\\\``."""
    parts: list[str] = []
    current: list[str] = []
    chars = iter(text)
    for char in chars:
        if char == "\\":
            nxt = next(chars, "")
            if nxt in ("|", "\\"):
                current.append(nxt)
            else:
                current.append(char + nxt)
        elif char == "|":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def parse_metadata(content: str | None) -> dict[str, Any] | None:
    """Return ``{"type": ..., "fields": {...}}`` for a message with a header,
    or ``None`` for plain text."""
    if not content:
        return None
    first_line = content.split("\n", 1)[0].rstrip("\r")
    match = HEADER_RE.fullmatch(first_line.rstrip())
    if not match:
        return None
    fields: dict[str, str] = {}
    for segment in _split_fields(match.group(2) or ""):
        key, sep, value = segment.partition(":")
        key = key.strip()
        if sep and KEY_RE.fullmatch(key):
            fields[key.lower()] = value.strip()
    return {"type": match.group(1).upper(), "fields": fields}


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def format_header(message_type: str, fields: Mapping[str, Any] | None = None) -> str:
    """Build a header line; raises ``ValueError`` for an invalid type or field."""
    message_type = message_type.strip()
    if not TYPE_RE.fullmatch(message_type):
        raise ValueError(
            f"Invalid message type '{message_type}': use a letter followed by up "
            "to 31 letters, digits, '_' or '-' (e.g. TASK, RESULT)"
        )
    parts: list[str] = []
    for key, value in (fields or {}).items():
        key = str(key).strip()
        if not KEY_RE.fullmatch(key):
            raise ValueError(
                f"Invalid metadata key '{key}': use a letter followed by letters, "
                "digits, '_' or '-'"
            )
        text = str(value).strip()
        if "\n" in text or "\r" in text:
            raise ValueError(f"Metadata value for '{key}' must be a single line")
        parts.append(f"{key.lower()}:{_escape(text)}")
    header = f"[{message_type.upper()}]"
    return f"{header} {' | '.join(parts)}" if parts else header


def matches(
    content: str | None,
    message_type: str | None = None,
    fields: Mapping[str, Any] | None = None,
) -> bool:
    """Whether ``content`` has a header matching the type and every field.

    Comparisons are case-insensitive. With no criteria, everything matches.
    """
    if not message_type and not fields:
        return True
    meta = parse_metadata(content)
    if meta is None:
        return False
    if message_type and meta["type"] != message_type.strip().upper():
        return False
    for key, value in (fields or {}).items():
        actual = meta["fields"].get(str(key).strip().lower())
        if actual is None or actual.lower() != str(value).strip().lower():
            return False
    return True
