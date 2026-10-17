from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastmcp import Client

from mock_stoat import MockStoat, create_app
from stoat_mcp.config import Settings
from stoat_mcp.server import create_server

HQ = "01HSERVER00000000000000000"
LAB = "01HSERVERB0000000000000000"
GENERAL = "01HGENERA00000000000000000"
RESEARCH = "01HRESEAR00000000000000000"
ALICE = "01HA11CE000000000000000000"
SERVICE_BOT = "01HB0TSERV1CE0000000000000"
RESEARCHER_BOT = "01HB0TRESEARCHER0000000000"
REVIEWER_BOT = "01HB0TREV1EWER000000000000"

BASE_ENV = {
    "STOAT_API_URL": "http://stoat.test",
    "STOAT_BOT_TOKEN": "service-token",
    "STOAT_BOT_IDENTITIES": "researcher=researcher-token,reviewer=reviewer-token",
    "STOAT_SERVER_IDS": HQ,
    "STOAT_RATE_LIMIT_PER_SECOND": "0",
}


def make_settings(**overrides: str) -> Settings:
    return Settings.from_env({**BASE_ENV, **overrides})


@pytest.fixture
def mock() -> MockStoat:
    return MockStoat()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def transport(mock: MockStoat) -> httpx.AsyncBaseTransport:
    """Routes the server's HTTP calls to the in-process mock."""
    return httpx.ASGITransport(app=create_app(mock))


@pytest.fixture
def mcp_server(settings: Settings, transport: httpx.AsyncBaseTransport):
    return create_server(settings, transport=transport)


@pytest.fixture
async def mcp_client(mcp_server):
    async with Client(mcp_server) as client:
        yield client


async def call(client: Client, name: str, **args: Any) -> Any:
    result = await client.call_tool(name, args)
    return result.data if result.data is not None else result.structured_content
