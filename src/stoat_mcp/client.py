"""Async client for the Stoat (Revolt) REST API and Autumn file server."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
from cachetools import TTLCache

from .config import Settings

ULID_RE = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}", re.IGNORECASE)
TEXT_CHANNEL_TYPES = {"TextChannel", "Group", "DirectMessage", "SavedMessages"}
MAX_RATE_LIMIT_RETRIES = 3


class StoatError(RuntimeError):
    """An error that is safe to surface to the calling agent."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def is_ulid(value: str) -> bool:
    return bool(ULID_RE.fullmatch(value))


class TokenBucket:
    """A simple async token bucket used to pace outgoing API requests."""

    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.capacity = max(1, burst)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.rate <= 0:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.updated) * self.rate
                )
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class StoatClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(
            timeout=settings.request_timeout, transport=transport
        )
        self._bucket = TokenBucket(
            settings.rate_limit_per_second, settings.rate_limit_burst
        )
        # Keyed by identity name ("" for the default bot): bots can belong to
        # different servers, so each sees its own channel list.
        self._channels: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            maxsize=64, ttl=settings.cache_ttl
        )
        self._users: TTLCache[str, dict[str, Any]] = TTLCache(
            maxsize=2048, ttl=settings.cache_ttl
        )
        self._autumn_url: str | None = settings.autumn_url

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ HTTP

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        identity: str | None = None,
        authenticated: bool = True,
        **kwargs: Any,
    ) -> Any:
        url = (
            path_or_url
            if path_or_url.startswith(("http://", "https://"))
            else f"{self.settings.api_url}{path_or_url}"
        )
        headers = dict(kwargs.pop("headers", {}) or {})
        if authenticated:
            headers["x-bot-token"] = self.settings.token_for(identity)

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            await self._bucket.acquire()
            try:
                res = await self._http.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise StoatError(f"Could not reach Stoat at {url}: {exc}") from exc

            if res.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                await asyncio.sleep(_retry_after_seconds(res))
                continue
            if res.is_error:
                raise StoatError(
                    _describe_error(method, path_or_url, res), status=res.status_code
                )
            if res.status_code == 204 or not res.content:
                return None
            return res.json()

        raise StoatError(f"{method} {path_or_url}: rate limited by Stoat")

    # ------------------------------------------------------------- Channels

    async def list_channels(
        self, server_id: str | None = None, identity: str | None = None
    ) -> list[dict[str, Any]]:
        """Return channels visible to ``identity`` in the configured servers
        (or in one given server)."""
        if server_id:
            return await self._fetch_server_channels(server_id, identity)

        key = _cache_key(identity)
        cached = self._channels.get(key)
        if cached is not None:
            return cached

        if not self.settings.server_ids:
            raise StoatError(
                "No servers configured. Set STOAT_SERVER_IDS to the server IDs "
                "the bot should index, or pass server_id explicitly."
            )
        channels: list[dict[str, Any]] = []
        for sid in self.settings.server_ids:
            try:
                channels.extend(await self._fetch_server_channels(sid, identity))
            except StoatError as exc:
                if exc.status != 404:
                    raise
                # This bot is not a member of that server; it sees no channels there.
        self._channels[key] = channels
        return channels

    async def _fetch_server_channels(
        self, server_id: str, identity: str | None = None
    ) -> list[dict[str, Any]]:
        server = await self.request(
            "GET",
            f"/servers/{server_id}",
            params={"include_channels": "true"},
            identity=identity,
        )
        raw = server.get("channels", [])
        channels: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                item = await self.request("GET", f"/channels/{item}", identity=identity)
            channels.append(item)
        for channel in channels:
            channel.setdefault("server", server_id)
            channel["_server_name"] = server.get("name")
        return channels

    async def resolve_channel_id(self, channel: str, identity: str | None = None) -> str:
        """Resolve ``#name``, ``name`` or a raw channel ID to a channel ID,
        among the channels ``identity`` can see."""
        name = channel.strip().lstrip("#").strip()
        if not name:
            raise StoatError("Channel must not be empty")
        if is_ulid(name):
            return name.upper()

        channels = await self.list_channels(identity=identity)
        matches = [c for c in channels if (c.get("name") or "").lower() == name.lower()]
        if not matches:
            # The cache may be stale (e.g. a channel was just created).
            self._channels.pop(_cache_key(identity), None)
            channels = await self.list_channels(identity=identity)
            matches = [
                c for c in channels if (c.get("name") or "").lower() == name.lower()
            ]
        if not matches:
            known = ", ".join(sorted({f"#{c.get('name')}" for c in channels if c.get("name")}))
            raise StoatError(f"No channel named '#{name}'. Known channels: {known or 'none'}")
        if len(matches) > 1:
            options = ", ".join(
                f"{c['_id']} (server {c.get('_server_name') or c.get('server')})"
                for c in matches
            )
            raise StoatError(
                f"Channel name '#{name}' is ambiguous; use one of these IDs: {options}"
            )
        return matches[0]["_id"]

    # ---------------------------------------------------------------- Users

    def _remember_users(self, users: list[dict[str, Any]]) -> None:
        for user in users:
            if user.get("_id"):
                self._users[user["_id"]] = user

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        user = self._users.get(user_id)
        if user is not None:
            return user
        try:
            user = await self.request("GET", f"/users/{user_id}")
        except StoatError:
            return None
        self._users[user_id] = user
        return user

    async def _author_names(
        self, messages: list[dict[str, Any]], members: list[dict[str, Any]]
    ) -> dict[str, str]:
        nicknames = {
            m["_id"]["user"]: m["nickname"]
            for m in members
            if isinstance(m.get("_id"), dict) and m.get("nickname")
        }
        names: dict[str, str] = {}
        for author_id in {m.get("author") for m in messages if m.get("author")}:
            if author_id in nicknames:
                names[author_id] = nicknames[author_id]
                continue
            user = await self.get_user(author_id)
            if user:
                names[author_id] = user.get("display_name") or user.get("username") or author_id
        return names

    async def format_messages(self, payload: Any) -> list[dict[str, Any]]:
        """Normalise a message list response into compact, agent-friendly dicts."""
        if isinstance(payload, dict):
            messages = payload.get("messages", [])
            self._remember_users(payload.get("users", []))
            members = payload.get("members", [])
        else:
            messages, members = payload or [], []

        names = await self._author_names(messages, members)
        result = []
        for msg in messages:
            author_id = msg.get("author")
            masquerade = (msg.get("masquerade") or {}).get("name")
            item: dict[str, Any] = {
                "message_id": msg.get("_id"),
                "channel_id": msg.get("channel"),
                "author_id": author_id,
                "author": masquerade or names.get(author_id, author_id),
                "content": msg.get("content") or "",
                "timestamp": ulid_timestamp(msg.get("_id")),
            }
            if msg.get("edited"):
                item["edited"] = msg["edited"]
            if msg.get("replies"):
                item["reply_to"] = msg["replies"]
            if msg.get("attachments"):
                item["attachments"] = [
                    {"id": a.get("_id"), "filename": a.get("filename"), "size": a.get("size")}
                    for a in msg["attachments"]
                ]
            if msg.get("system"):
                item["system"] = msg["system"]
            result.append(item)
        return result

    # ------------------------------------------------------------- Messages

    async def fetch_messages(
        self, channel_id: str, limit: int, before: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": limit,
            "sort": "Latest",
            "include_users": "true",
        }
        if before:
            params["before"] = before
        payload = await self.request("GET", f"/channels/{channel_id}/messages", params=params)
        messages = await self.format_messages(payload)
        messages.reverse()  # API returns newest first; agents want chronological.
        return messages

    async def search_messages(
        self, channel_id: str, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """Find messages containing every word of ``query`` (case-insensitive).

        Stoat's ``/channels/{id}/search`` route rejects bot accounts, so this
        scans the most recent ``search_scan_limit`` messages of the channel
        instead. Results are newest first.
        """
        terms = query.lower().split()
        hits: list[dict[str, Any]] = []
        users: list[dict[str, Any]] = []
        members: list[dict[str, Any]] = []
        before: str | None = None
        scanned = 0
        while scanned < self.settings.search_scan_limit and len(hits) < limit:
            page_size = min(100, self.settings.search_scan_limit - scanned)
            params: dict[str, Any] = {
                "limit": page_size,
                "sort": "Latest",
                "include_users": "true",
            }
            if before:
                params["before"] = before
            payload = await self.request(
                "GET", f"/channels/{channel_id}/messages", params=params
            )
            if isinstance(payload, dict):
                page = payload.get("messages", [])
                users.extend(payload.get("users", []))
                members.extend(payload.get("members", []))
            else:
                page = payload or []
            if not page:
                break
            scanned += len(page)
            hits.extend(
                m for m in page
                if all(t in (m.get("content") or "").lower() for t in terms)
            )
            before = page[-1]["_id"]
            if len(page) < page_size:
                break
        return await self.format_messages(
            {"messages": hits[:limit], "users": users, "members": members}
        )

    async def send_message(
        self,
        channel_id: str,
        content: str | None,
        *,
        reply_to: str | None = None,
        attachments: list[str] | None = None,
        identity: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if content:
            body["content"] = content
        if attachments:
            body["attachments"] = attachments
        if reply_to:
            body["replies"] = [{"id": reply_to, "mention": False}]
        return await self.request(
            "POST", f"/channels/{channel_id}/messages", json=body, identity=identity
        )

    # ---------------------------------------------------------------- Files

    async def autumn_url(self) -> str:
        if self._autumn_url:
            return self._autumn_url
        root = await self.request("GET", "/", authenticated=False)
        autumn = (root or {}).get("features", {}).get("autumn", {})
        if not autumn.get("enabled", True) or not autumn.get("url"):
            raise StoatError(
                "File uploads are not enabled on this Stoat instance "
                "(or set STOAT_AUTUMN_URL explicitly)."
            )
        self._autumn_url = autumn["url"].rstrip("/")
        return self._autumn_url

    async def upload_attachment(
        self, filename: str, data: bytes, identity: str | None = None
    ) -> str:
        if len(data) > self.settings.max_upload_bytes:
            raise StoatError(
                f"file is {len(data):,} bytes; the limit is "
                f"{self.settings.max_upload_bytes:,}"
            )
        base = await self.autumn_url()
        res = await self.request(
            "POST",
            f"{base}/attachments",
            files={"file": (filename, data)},
            identity=identity,
        )
        return res["id"]


def _cache_key(identity: str | None) -> str:
    return identity.strip().lower() if identity else ""


def ulid_timestamp(ulid: str | None) -> str | None:
    """Decode the millisecond timestamp embedded in a ULID as ISO-8601 UTC."""
    if not ulid or not is_ulid(ulid):
        return None
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    millis = 0
    for char in ulid[:10].upper():
        millis = millis * 32 + alphabet.index(char)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(millis / 1000)) + (
        f".{millis % 1000:03d}Z"
    )


def _retry_after_seconds(res: httpx.Response) -> float:
    try:
        retry_ms = float(res.json().get("retry_after", 1000))
    except (ValueError, AttributeError):
        retry_ms = float(res.headers.get("x-ratelimit-reset-after", 1000))
    return min(max(retry_ms / 1000, 0.05), 30.0)


def _describe_error(method: str, path: str, res: httpx.Response) -> str:
    detail = ""
    try:
        data = res.json()
        if isinstance(data, dict):
            detail = data.get("type") or data.get("error") or ""
            if data.get("permission"):
                detail += f" (missing permission: {data['permission']})"
    except ValueError:
        detail = res.text[:200]
    return f"{method} {path} failed with HTTP {res.status_code}{': ' + detail if detail else ''}"
