import pytest

from stoat_mcp.metadata import format_header, matches, parse_metadata


def test_parse_task_header_and_body():
    meta = parse_metadata("[TASK] id:42 | priority:high\nResearch the EU market")
    assert meta == {"type": "TASK", "fields": {"id": "42", "priority": "high"}}


def test_parse_normalises_case_and_whitespace():
    meta = parse_metadata("[result]   ID : 42 |Status:Complete  ")
    assert meta == {"type": "RESULT", "fields": {"id": "42", "status": "Complete"}}


def test_parse_type_only_and_crlf():
    assert parse_metadata("[NOTE]\r\nbody") == {"type": "NOTE", "fields": {}}


@pytest.mark.parametrize("content", [
    "", None, "plain text", "see [TASK] id:1", "[TASK](https://example.com) link",
    "[ ] checklist item", "[1] footnote", "\n[TASK] id:1",
])
def test_plain_text_has_no_metadata(content):
    assert parse_metadata(content) is None


def test_parse_ignores_malformed_segments_and_keeps_colons_in_values():
    meta = parse_metadata("[TASK] Draft the brief | id:7 | 9bad:x | url:http://a/b")
    assert meta["fields"] == {"id": "7", "url": "http://a/b"}


def test_escaped_pipe_and_backslash_round_trip():
    header = format_header("result", {"id": "a|b", "path": "C:\\tmp"})
    assert header == "[RESULT] id:a\\|b | path:C:\\\\tmp"
    assert parse_metadata(header)["fields"] == {"id": "a|b", "path": "C:\\tmp"}


def test_format_header_without_fields():
    assert format_header("task") == "[TASK]"


@pytest.mark.parametrize("message_type, fields", [
    ("", None), ("1TASK", None), ("TA SK", None), ("T" * 33, None),
    ("TASK", {"bad key": "x"}), ("TASK", {"id": "a\nb"}),
])
def test_format_header_rejects_invalid_input(message_type, fields):
    with pytest.raises(ValueError):
        format_header(message_type, fields)


def test_matches():
    content = "[RESULT] id:42 | status:complete\nDone"
    assert matches(content)
    assert matches("plain", None, None)
    assert matches(content, "result")
    assert matches(content, "RESULT", {"ID": "42", "status": "COMPLETE"})
    assert not matches(content, "TASK")
    assert not matches(content, None, {"id": "43"})
    assert not matches(content, None, {"priority": "high"})
    assert not matches("plain", "RESULT")
