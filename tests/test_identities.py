"""Two agents acting under different bot identities must never be mixed up.

Each check looks at what the mock Stoat recorded, i.e. which token actually
reached the API and which bot user authored each message or owns each file,
rather than trusting what the tool reported back.
"""

import asyncio
import base64

from fastmcp import Client

from .conftest import GENERAL, RESEARCHER_BOT, REVIEWER_BOT, SERVICE_BOT, call, make_settings
from mock_stoat import MockStoat
from stoat_mcp.server import create_server

AGENTS = {
    "researcher": RESEARCHER_BOT,
    "reviewer": REVIEWER_BOT,
}


def authors_by_tag(mock: MockStoat) -> dict[str, set[str]]:
    """Map the agent tag at the start of each message to the bot(s) that wrote it."""
    seen: dict[str, set[str]] = {}
    for msg in mock.all_messages():
        tag = msg["content"].split(":", 1)[0]
        if tag in AGENTS:
            seen.setdefault(tag, set()).add(msg["author"])
    return seen


async def test_concurrent_agents_post_as_their_own_identity(mcp_server, mock):
    """Two agent sessions sharing one MCP server post interleaved messages."""

    async def agent(identity: str, rounds: int) -> None:
        async with Client(mcp_server) as client:
            await asyncio.gather(*(
                call(client, "stoat_post_message", channel="#general",
                     content=f"{identity}: update {i}", identity=identity)
                for i in range(rounds)
            ))

    await asyncio.gather(agent("researcher", 25), agent("reviewer", 25))

    assert authors_by_tag(mock) == {"researcher": {RESEARCHER_BOT}, "reviewer": {REVIEWER_BOT}}
    posts = [r for r in mock.requests if r["method"] == "POST" and r["path"].endswith("/messages")]
    assert {r["token"] for r in posts} == {"researcher-token", "reviewer-token"}
    assert len(posts) == 50


async def test_concurrent_chunked_messages_keep_one_author_per_message(mock, transport):
    """Every chunk of a split message is sent as the identity that sent the first."""
    settings = make_settings(STOAT_MESSAGE_LIMIT="120")
    server = create_server(settings, transport=transport)
    long_text = {name: "\n".join(f"{name}: line {i} " + "z" * 40 for i in range(4)) for name in AGENTS}

    async def agent(identity: str):
        async with Client(server) as client:
            return await call(client, "stoat_post_message", channel=GENERAL,
                              content=long_text[identity], identity=identity)

    results = dict(zip(AGENTS, await asyncio.gather(*(agent(name) for name in AGENTS))))

    by_id = {m["_id"]: m for m in mock.all_messages()}
    for name, bot_id in AGENTS.items():
        ids = results[name]["message_ids"]
        assert len(ids) > 1
        assert {by_id[i]["author"] for i in ids} == {bot_id}
        assert "".join(by_id[i]["content"] for i in ids) == long_text[name]


async def test_concurrent_uploads_stay_with_their_identity(mcp_server, mock):
    """Files are uploaded and attached under the same identity (the mock rejects
    attaching someone else's upload, so a mix-up would fail the post)."""

    async def agent(identity: str):
        async with Client(mcp_server) as client:
            return await asyncio.gather(*(
                call(client, "stoat_upload_file", channel=GENERAL, filename=f"{identity}-{i}.txt",
                     file_bytes_base64=base64.b64encode(f"{identity} file {i}".encode()).decode(),
                     content=f"{identity}: file {i}", identity=identity)
                for i in range(5)
            ))

    results = dict(zip(AGENTS, await asyncio.gather(*(agent(name) for name in AGENTS))))

    by_id = {m["_id"]: m for m in mock.all_messages()}
    for name, bot_id in AGENTS.items():
        for result in results[name]:
            assert by_id[result["message_id"]]["author"] == bot_id
            attachment = mock.attachments[result["attachment_id"]]
            assert attachment["uploader"] == bot_id
            assert attachment["data"].startswith(name.encode())


async def test_identity_does_not_leak_into_later_calls(mcp_client, mock):
    """After posting as an identity, a call without one uses the default bot."""
    await call(mcp_client, "stoat_post_message", channel=GENERAL, content="researcher: hi", identity="researcher")
    await call(mcp_client, "stoat_post_message", channel=GENERAL, content="service: hi")
    await call(mcp_client, "stoat_post_message", channel=GENERAL, content="reviewer: hi", identity="reviewer")
    await call(mcp_client, "stoat_post_message", channel=GENERAL, content="service: again")

    authors = [m["author"] for m in mock.messages[GENERAL][1:]]
    assert authors == [RESEARCHER_BOT, SERVICE_BOT, REVIEWER_BOT, SERVICE_BOT]


async def test_history_attributes_each_agent_correctly(mcp_client):
    await call(mcp_client, "stoat_post_message", channel=GENERAL, content="researcher: found it", identity="researcher")
    await call(mcp_client, "stoat_post_message", channel=GENERAL, content="reviewer: looks good", identity="reviewer")

    history = await call(mcp_client, "stoat_read_history", channel=GENERAL, limit=2)
    assert [(m["author"], m["author_id"]) for m in history] == [
        ("Research Agent", RESEARCHER_BOT),
        ("Review Agent", REVIEWER_BOT),
    ]


async def test_identity_only_config_resolves_channel_names(mock, transport):
    """With no default bot token, names resolve using the selected identity."""
    settings = make_settings(STOAT_BOT_TOKEN="")
    async with Client(create_server(settings, transport=transport)) as client:
        await call(client, "stoat_post_message", channel="#general", content="researcher: hi", identity="researcher")
        await call(client, "stoat_upload_file", channel="#general", filename="r.txt",
                   file_bytes_base64=base64.b64encode(b"r").decode(), identity="reviewer")
    authors = [m["author"] for m in mock.messages[GENERAL][1:]]
    assert authors == [RESEARCHER_BOT, REVIEWER_BOT]
    assert {r["token"] for r in mock.requests if r["path"] != "/"} == {"researcher-token", "reviewer-token"}


async def test_channel_names_resolve_among_the_identitys_own_servers(mock, transport):
    """#general exists in HQ and Lab. Only the service bot is in Lab, so the name
    is ambiguous for it but not for the researcher."""
    settings = make_settings(STOAT_SERVER_IDS="01HSERVER00000000000000000,01HSERVERB0000000000000000")
    async with Client(create_server(settings, transport=transport)) as client:
        result = await call(client, "stoat_post_message", channel="#general", content="researcher: hi",
                            identity="researcher")
        assert result["channel_id"] == GENERAL
        res = await client.call_tool("stoat_post_message", {"channel": "#general", "content": "x"},
                                     raise_on_error=False)
        assert res.is_error and "ambiguous" in res.content[0].text
