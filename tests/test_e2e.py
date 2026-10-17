"""End-to-end: mock Stoat and stoat-mcp run as real processes talking HTTP.

Two MCP clients connect over Streamable HTTP, as two separate agents would,
and post under different identities. The result is checked through the mock's
own API using each bot's token, independently of stoat-mcp.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from .conftest import GENERAL, HQ, RESEARCHER_BOT, REVIEWER_BOT, call

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url: str, proc: subprocess.Popen | None = None, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"{proc.args} exited early:\n{proc.stdout.read().decode()}")
        try:
            httpx.get(url, timeout=0.5)
            return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise TimeoutError(url)


def spawn(module: str, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", module],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}", **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


@pytest.fixture(scope="module")
def stack():
    if os.environ.get("E2E_STOAT_API") and os.environ.get("E2E_MCP_URL"):
        # Already running, e.g. under docker-compose.test.yml.
        wait_for(f"{os.environ['E2E_STOAT_API']}/")
        wait_for(os.environ["E2E_MCP_URL"])
        yield {"api": os.environ["E2E_STOAT_API"], "mcp": os.environ["E2E_MCP_URL"]}
        return

    mock_port, mcp_port = free_port(), free_port()
    mock = spawn("mock_stoat", {"MOCK_STOAT_PORT": str(mock_port)})
    mcp = spawn("stoat_mcp", {
        "STOAT_API_URL": f"http://127.0.0.1:{mock_port}",
        "STOAT_BOT_TOKEN": "service-token",
        "STOAT_BOT_IDENTITIES": "researcher=researcher-token,reviewer=reviewer-token",
        "STOAT_SERVER_IDS": HQ,
        "STOAT_MCP_TRANSPORT": "http",
        "STOAT_MCP_PORT": str(mcp_port),
    })
    try:
        wait_for(f"http://127.0.0.1:{mock_port}/", mock)
        wait_for(f"http://127.0.0.1:{mcp_port}/mcp", mcp)
        yield {"api": f"http://127.0.0.1:{mock_port}", "mcp": f"http://127.0.0.1:{mcp_port}/mcp"}
    finally:
        for proc in (mcp, mock):
            proc.terminate()
            proc.wait(timeout=10)


async def test_two_agents_over_http_keep_their_identities(stack):
    async def agent(identity: str) -> list[str]:
        async with Client(stack["mcp"]) as client:
            results = await asyncio.gather(*(
                call(client, "stoat_post_message", channel="#general",
                     content=f"{identity}: e2e {i}", identity=identity)
                for i in range(10)
            ))
            return [mid for r in results for mid in r["message_ids"]]

    ids = dict(zip(("researcher", "reviewer"),
                   await asyncio.gather(agent("researcher"), agent("reviewer"))))

    # Read back through the mock API directly, as the service bot.
    async with httpx.AsyncClient(base_url=stack["api"], headers={"x-bot-token": "service-token"}) as api:
        res = await api.get(f"/channels/{GENERAL}/messages", params={"limit": 100})
        messages = {m["_id"]: m for m in res.json()}

    expected = {"researcher": RESEARCHER_BOT, "reviewer": REVIEWER_BOT}
    for identity, bot_id in expected.items():
        assert len(ids[identity]) == 10
        for mid in ids[identity]:
            assert messages[mid]["author"] == bot_id
            assert messages[mid]["content"].startswith(f"{identity}:")

    async with Client(stack["mcp"]) as client:
        history = await call(client, "stoat_read_history", channel="general", limit=100)
    names = {m["author"] for m in history if m["content"].startswith(("researcher:", "reviewer:"))}
    assert names == {"Research Agent", "Review Agent"}
