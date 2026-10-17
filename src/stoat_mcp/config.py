"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_identities(value: str | None) -> dict[str, str]:
    """Parse ``name=token,name2=token2`` into a mapping."""
    identities: dict[str, str] = {}
    for item in _split_list(value):
        name, sep, token = item.partition("=")
        if not sep or not name.strip() or not token.strip():
            raise ValueError(
                "STOAT_BOT_IDENTITIES entries must look like 'name=token'"
            )
        identities[name.strip().lower()] = token.strip()
    return identities


@dataclass(frozen=True)
class Settings:
    api_url: str
    bot_token: str | None
    identities: dict[str, str] = field(default_factory=dict)
    server_ids: list[str] = field(default_factory=list)
    autumn_url: str | None = None
    message_limit: int = 2000
    max_chunks: int = 8
    max_upload_bytes: int = 20 * 1024 * 1024
    cache_ttl: int = 600
    request_timeout: float = 15.0
    rate_limit_per_second: float = 5.0
    rate_limit_burst: int = 10
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8080

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        return cls(
            api_url=env.get("STOAT_API_URL", "http://stoat-api:8000").rstrip("/"),
            bot_token=env.get("STOAT_BOT_TOKEN") or None,
            identities=_parse_identities(env.get("STOAT_BOT_IDENTITIES")),
            server_ids=_split_list(env.get("STOAT_SERVER_IDS")),
            autumn_url=(env.get("STOAT_AUTUMN_URL") or "").rstrip("/") or None,
            message_limit=int(env.get("STOAT_MESSAGE_LIMIT", "2000")),
            max_chunks=int(env.get("STOAT_MAX_CHUNKS", "8")),
            max_upload_bytes=int(env.get("STOAT_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
            cache_ttl=int(env.get("STOAT_CACHE_TTL", "600")),
            request_timeout=float(env.get("STOAT_REQUEST_TIMEOUT", "15")),
            rate_limit_per_second=float(env.get("STOAT_RATE_LIMIT_PER_SECOND", "5")),
            rate_limit_burst=int(env.get("STOAT_RATE_LIMIT_BURST", "10")),
            transport=env.get("STOAT_MCP_TRANSPORT", "stdio"),
            host=env.get("STOAT_MCP_HOST", "127.0.0.1"),
            port=int(env.get("STOAT_MCP_PORT", "8080")),
        )

    def token_for(self, identity: str | None = None) -> str:
        """Return the bot token for a named identity, or the default token."""
        if identity:
            token = self.identities.get(identity.strip().lower())
            if token is None:
                known = ", ".join(sorted(self.identities)) or "none configured"
                raise ValueError(
                    f"Unknown bot identity '{identity}'. Known identities: {known}"
                )
            return token
        if not self.bot_token:
            raise ValueError(
                "No Stoat bot token configured. Set STOAT_BOT_TOKEN."
            )
        return self.bot_token
