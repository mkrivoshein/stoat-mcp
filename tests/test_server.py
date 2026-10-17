import base64

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from stoat_mcp.client import ulid_timestamp
from stoat_mcp.config import Settings
from stoat_mcp.server import create_server

from .conftest import (
    ALICE, GENERAL, HQ, LAB, RESEARCH, SERVICE_BOT, call, make_settings,
)


async def test_tools_are_registered(mcp_client):
    names = {t.name for t in await mcp_client.list_tools()}
    assert names == {
        "stoat_post_message", "stoat_read_history", "stoat_list_channels",
        "stoat_search_messages", "stoat_upload_file", "stoat_list_identities",
    }


async def test_list_channels(mcp_client):
    channels = await call(mcp_client, "stoat_list_channels")
    by_name = {c["name"]: c for c in channels}
    assert set(by_name) == {"general", "market-research", "standup"}
    assert by_name["market-research"]["channel_id"] == RESEARCH
    assert by_name["market-research"]["topic"] == "Research output"
    assert by_name["general"]["server_name"] == "HQ"


async def test_post_message_resolves_channel_name(mcp_client, mock):
    result = await call(mcp_client, "stoat_post_message", channel="#market-research", content="hi")
    assert result["channel_id"] == RESEARCH
    [msg] = mock.messages[RESEARCH]
    assert msg["_id"] == result["message_ids"][0]
    assert msg["content"] == "hi"
    assert msg["author"] == SERVICE_BOT


async def test_post_message_chunks_and_replies_once(mock, transport):
    settings = make_settings(STOAT_MESSAGE_LIMIT="100")
    seed_id = mock.messages[GENERAL][0]["_id"]
    content = "\n".join(f"line {i:02d} " + "x" * 30 for i in range(6))
    async with Client(create_server(settings, transport=transport)) as client:
        result = await call(client, "stoat_post_message", channel="general", content=content, reply_to=seed_id)
    posted = mock.messages[GENERAL][1:]
    assert [m["_id"] for m in posted] == result["message_ids"]
    assert len(posted) > 1
    assert posted[0]["replies"] == [seed_id]
    assert all("replies" not in m for m in posted[1:])
    assert "".join(m["content"] for m in posted) == content


async def test_default_limit_chunks_under_real_2000_char_cap(mcp_client, mock):
    content = "\n\n".join("paragraph " + "y" * 900 for _ in range(5))
    result = await call(mcp_client, "stoat_post_message", channel=GENERAL, content=content)
    posted = mock.messages[GENERAL][1:]
    assert len(posted) == len(result["message_ids"]) >= 3
    assert all(len(m["content"]) <= 2000 for m in posted)


async def test_very_long_message_becomes_attachment(mock, transport):
    settings = make_settings(STOAT_MESSAGE_LIMIT="100", STOAT_MAX_CHUNKS="3")
    async with Client(create_server(settings, transport=transport)) as client:
        result = await call(client, "stoat_post_message", channel="general", content="word " * 200)
    assert result["delivered_as"] == "attachment"
    [msg] = mock.messages[GENERAL][1:]
    [attachment] = msg["attachments"]
    assert attachment["filename"] == "message.md"
    assert mock.attachments[attachment["_id"]]["data"] == ("word " * 200).encode()


async def test_unknown_identity_is_an_error_and_posts_nothing(mcp_client, mock):
    with pytest.raises(ToolError, match="Unknown bot identity"):
        await mcp_client.call_tool("stoat_post_message", {"channel": GENERAL, "content": "hi", "identity": "nobody"})
    assert len(mock.messages[GENERAL]) == 1


async def test_unknown_channel_lists_known_channels(mcp_client):
    with pytest.raises(ToolError, match="#market-research"):
        await mcp_client.call_tool("stoat_post_message", {"channel": "#nope", "content": "hi"})


async def test_ambiguous_channel_name(mock, transport):
    settings = make_settings(STOAT_SERVER_IDS=f"{HQ},{LAB}")
    async with Client(create_server(settings, transport=transport)) as client:
        with pytest.raises(ToolError, match="ambiguous"):
            await client.call_tool("stoat_post_message", {"channel": "#general", "content": "hi"})


async def test_invalid_token_is_reported(transport):
    settings = make_settings(STOAT_BOT_TOKEN="wrong")
    async with Client(create_server(settings, transport=transport)) as client:
        with pytest.raises(ToolError, match="401"):
            await client.call_tool("stoat_read_history", {"channel": GENERAL})


async def test_read_history_is_chronological_with_names(mcp_client):
    await call(mcp_client, "stoat_post_message", channel=GENERAL, content="on it")
    history = await call(mcp_client, "stoat_read_history", channel="#general", limit=10)
    assert [m["content"] for m in history] == ["Kick-off: please research the EU market", "on it"]
    assert [m["author"] for m in history] == ["Alice (PM)", "stoat-service"]
    assert history[0]["author_id"] == ALICE
    assert history[0]["timestamp"].endswith("Z")


async def test_read_history_paging(mcp_client):
    for i in range(5):
        await call(mcp_client, "stoat_post_message", channel=GENERAL, content=f"m{i}")
    latest = await call(mcp_client, "stoat_read_history", channel=GENERAL, limit=2)
    assert [m["content"] for m in latest] == ["m3", "m4"]
    older = await call(mcp_client, "stoat_read_history", channel=GENERAL, limit=2,
                       before=latest[0]["message_id"])
    assert [m["content"] for m in older] == ["m1", "m2"]


async def test_search_single_channel(mcp_client):
    await call(mcp_client, "stoat_post_message", channel="market-research", content="EU market notes")
    results = await call(mcp_client, "stoat_search_messages", query="market", channel="market-research")
    assert [m["content"] for m in results] == ["EU market notes"]


async def test_search_all_text_channels_skips_voice(mcp_client, mock):
    await call(mcp_client, "stoat_post_message", channel="market-research", content="EU market notes")
    results = await call(mcp_client, "stoat_search_messages", query="market")
    assert {m["content"] for m in results} == {"EU market notes", "Kick-off: please research the EU market"}
    searched = {r["path"] for r in mock.requests if r["path"].endswith("/search")}
    assert searched == {f"/channels/{GENERAL}/search", f"/channels/{RESEARCH}/search"}


async def test_upload_file(mcp_client, mock):
    data = base64.b64encode(b"# Report\n").decode()
    result = await call(mcp_client, "stoat_upload_file", channel="general", filename="report.md",
                        file_bytes_base64=data, content="See attached")
    [msg] = mock.messages[GENERAL][1:]
    assert msg["_id"] == result["message_id"]
    assert msg["content"] == "See attached"
    assert msg["attachments"][0]["_id"] == result["attachment_id"]
    assert mock.attachments[result["attachment_id"]]["data"] == b"# Report\n"


async def test_upload_rejects_bad_base64(mcp_client):
    with pytest.raises(ToolError, match="base64"):
        await mcp_client.call_tool("stoat_upload_file", {"channel": "general", "filename": "a", "file_bytes_base64": "!!"})


async def test_retries_after_rate_limit(mcp_client, mock):
    mock.inject_rate_limit(2)
    result = await call(mcp_client, "stoat_post_message", channel=GENERAL, content="hi")
    assert len(result["message_ids"]) == 1


async def test_list_identities(mcp_client):
    assert await call(mcp_client, "stoat_list_identities") == ["researcher", "reviewer"]


def test_ulid_timestamp():
    assert ulid_timestamp("01ARZ3NDEKTSV4RRFFQ69G5FAV") == "2016-07-30T23:54:10.259Z"
    assert ulid_timestamp("not-a-ulid") is None


def test_identity_config_validation():
    with pytest.raises(ValueError):
        Settings.from_env({"STOAT_BOT_IDENTITIES": "missing-token"})
