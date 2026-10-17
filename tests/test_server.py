import base64
import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from stoat_mcp.client import StoatClient, ulid_timestamp
from stoat_mcp.config import Settings
from stoat_mcp.server import create_server

SERVER = "01HSERVER00000000000000000"
GENERAL = "01HGENERA00000000000000000"
RESEARCH = "01HRESEAR00000000000000000"
ALICE = "01HA11CE000000000000000000"
BOT = "01HB0T00000000000000000000"


class FakeStoat:
    """A tiny in-memory stand-in for the Stoat REST API and Autumn."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.posted: list[dict] = []
        self.uploads: list[bytes] = []
        self.rate_limit_next = False
        self.messages = {
            GENERAL: [
                {"_id": "01HMSG0000000000000000000B", "channel": GENERAL, "author": BOT, "content": "second"},
                {"_id": "01HMSG0000000000000000000A", "channel": GENERAL, "author": ALICE, "content": "first"},
            ],
            RESEARCH: [
                {"_id": "01HMSG0000000000000000000C", "channel": RESEARCH, "author": ALICE, "content": "market notes"},
            ],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if self.rate_limit_next:
            self.rate_limit_next = False
            return httpx.Response(429, json={"retry_after": 10})
        if path == "/":
            return httpx.Response(200, json={"features": {"autumn": {"enabled": True, "url": "http://autumn.test"}}})
        if request.url.host == "autumn.test" and path == "/attachments":
            self.uploads.append(request.content)
            return httpx.Response(200, json={"id": f"file{len(self.uploads)}"})
        if path == f"/servers/{SERVER}":
            return httpx.Response(200, json={
                "_id": SERVER,
                "name": "HQ",
                "channels": [
                    {"_id": GENERAL, "name": "general", "channel_type": "TextChannel", "server": SERVER},
                    {"_id": RESEARCH, "name": "market-research", "channel_type": "TextChannel",
                     "description": "Research output", "server": SERVER},
                    {"_id": "01HV01CE000000000000000000", "name": "voice", "channel_type": "VoiceChannel",
                     "server": SERVER},
                ],
            })
        if path == f"/users/{ALICE}":
            return httpx.Response(200, json={"_id": ALICE, "username": "alice"})
        for channel_id, messages in self.messages.items():
            if path == f"/channels/{channel_id}/messages" and request.method == "GET":
                return httpx.Response(200, json={
                    "messages": messages,
                    "users": [{"_id": BOT, "username": "helper", "display_name": "Helper Bot"}],
                    "members": [],
                })
            if path == f"/channels/{channel_id}/messages" and request.method == "POST":
                body = json.loads(request.content)
                body["_token"] = request.headers["x-bot-token"]
                self.posted.append(body)
                return httpx.Response(200, json={"_id": f"01HP0STED{len(self.posted):017d}"})
            if path == f"/channels/{channel_id}/search":
                query = json.loads(request.content)["query"]
                hits = [m for m in messages if query in m["content"]]
                return httpx.Response(200, json={"messages": hits, "users": [], "members": []})
        return httpx.Response(404, json={"type": "NotFound"})


@pytest.fixture
def fake():
    return FakeStoat()


@pytest.fixture
def settings():
    return Settings.from_env({
        "STOAT_API_URL": "http://stoat.test",
        "STOAT_BOT_TOKEN": "default-token",
        "STOAT_BOT_IDENTITIES": "researcher=research-token",
        "STOAT_SERVER_IDS": SERVER,
        "STOAT_MESSAGE_LIMIT": "100",
        "STOAT_MAX_CHUNKS": "3",
        "STOAT_RATE_LIMIT_PER_SECOND": "0",
    })


@pytest.fixture
async def mcp_client(fake, settings):
    stoat = StoatClient(settings, transport=httpx.MockTransport(fake.handler))
    async with Client(create_server(settings, stoat)) as client:
        yield client
    await stoat.aclose()


async def call(client, name, **args):
    result = await client.call_tool(name, args)
    return result.data if result.data is not None else result.structured_content


async def test_tools_are_registered(mcp_client):
    names = {t.name for t in await mcp_client.list_tools()}
    assert names == {
        "stoat_post_message", "stoat_read_history", "stoat_list_channels",
        "stoat_search_messages", "stoat_upload_file", "stoat_list_identities",
    }


async def test_list_channels(mcp_client):
    channels = await call(mcp_client, "stoat_list_channels")
    by_name = {c["name"]: c for c in channels}
    assert by_name["market-research"]["channel_id"] == RESEARCH
    assert by_name["market-research"]["topic"] == "Research output"
    assert by_name["general"]["server_name"] == "HQ"


async def test_post_message_resolves_channel_name(mcp_client, fake):
    result = await call(mcp_client, "stoat_post_message", channel="#market-research", content="hi")
    assert result["channel_id"] == RESEARCH
    assert len(result["message_ids"]) == 1
    assert fake.posted == [{"content": "hi", "_token": "default-token"}]


async def test_post_message_chunks_and_replies_once(mcp_client, fake):
    content = "\n".join(f"line {i:02d} " + "x" * 30 for i in range(6))
    result = await call(mcp_client, "stoat_post_message", channel="general", content=content,
                        reply_to="01HMSG0000000000000000000A")
    assert len(result["message_ids"]) == len(fake.posted) > 1
    assert fake.posted[0]["replies"] == [{"id": "01HMSG0000000000000000000A", "mention": False}]
    assert all("replies" not in p for p in fake.posted[1:])
    assert all(len(p["content"]) <= 100 for p in fake.posted)
    assert "".join(p["content"] for p in fake.posted) == content


async def test_very_long_message_becomes_attachment(mcp_client, fake):
    content = "word " * 200
    result = await call(mcp_client, "stoat_post_message", channel="general", content=content)
    assert result["delivered_as"] == "attachment"
    assert len(fake.uploads) == 1
    assert fake.posted[0]["attachments"] == ["file1"]


async def test_post_with_named_identity(mcp_client, fake):
    await call(mcp_client, "stoat_post_message", channel=GENERAL, content="hi", identity="Researcher")
    assert fake.posted[0]["_token"] == "research-token"


async def test_unknown_identity_is_an_error(mcp_client):
    with pytest.raises(ToolError, match="Unknown bot identity"):
        await mcp_client.call_tool("stoat_post_message", {"channel": GENERAL, "content": "hi", "identity": "nobody"})


async def test_unknown_channel_lists_known_channels(mcp_client):
    with pytest.raises(ToolError, match="#market-research"):
        await mcp_client.call_tool("stoat_post_message", {"channel": "#nope", "content": "hi"})


async def test_read_history_is_chronological_with_names(mcp_client):
    history = await call(mcp_client, "stoat_read_history", channel="#general", limit=10)
    assert [m["content"] for m in history] == ["first", "second"]
    assert [m["author"] for m in history] == ["alice", "Helper Bot"]
    assert history[0]["timestamp"].endswith("Z")


async def test_read_history_passes_paging(mcp_client, fake):
    await call(mcp_client, "stoat_read_history", channel=GENERAL, limit=5, before="01HMSG0000000000000000000B")
    history_request = next(r for r in fake.requests if r.url.path.endswith("/messages"))
    params = history_request.url.params
    assert params["limit"] == "5"
    assert params["before"] == "01HMSG0000000000000000000B"


async def test_search_single_channel(mcp_client):
    results = await call(mcp_client, "stoat_search_messages", query="market", channel="market-research")
    assert [m["content"] for m in results] == ["market notes"]


async def test_search_all_text_channels(mcp_client, fake):
    results = await call(mcp_client, "stoat_search_messages", query="s")
    assert {m["content"] for m in results} == {"second", "first", "market notes"}
    searched = {r.url.path for r in fake.requests if r.url.path.endswith("/search")}
    assert searched == {f"/channels/{GENERAL}/search", f"/channels/{RESEARCH}/search"}


async def test_upload_file(mcp_client, fake):
    data = base64.b64encode(b"# Report\n").decode()
    result = await call(mcp_client, "stoat_upload_file", channel="general", filename="report.md",
                        file_bytes_base64=data, content="See attached")
    assert result["attachment_id"] == "file1"
    assert b"# Report" in fake.uploads[0]
    assert fake.posted[0]["attachments"] == ["file1"]
    assert fake.posted[0]["content"] == "See attached"


async def test_upload_rejects_bad_base64(mcp_client):
    with pytest.raises(ToolError, match="base64"):
        await mcp_client.call_tool("stoat_upload_file", {"channel": "general", "filename": "a", "file_bytes_base64": "!!"})


async def test_retries_after_rate_limit(mcp_client, fake):
    fake.rate_limit_next = True
    result = await call(mcp_client, "stoat_post_message", channel=GENERAL, content="hi")
    assert len(result["message_ids"]) == 1


async def test_list_identities(mcp_client):
    assert await call(mcp_client, "stoat_list_identities") == ["researcher"]


def test_ulid_timestamp():
    assert ulid_timestamp("01ARZ3NDEKTSV4RRFFQ69G5FAV") == "2016-07-30T23:54:10.259Z"
    assert ulid_timestamp("not-a-ulid") is None


def test_identity_config_validation():
    with pytest.raises(ValueError):
        Settings.from_env({"STOAT_BOT_IDENTITIES": "missing-token"})
