"""FastMCP tool definitions for Stoat."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .chunking import split_message
from .client import TEXT_CHANNEL_TYPES, StoatClient, StoatError
from .config import Settings

INSTRUCTIONS = """\
Tools for reading and posting to a private Stoat (Revolt) chat instance.
Channels can be given as a raw ID or by name (e.g. "#general" or "general").
Use stoat_list_channels to discover channels, stoat_read_history for context,
and stoat_post_message to deliver results. Long messages are split
automatically. Pass `identity` to post as one of the configured bot identities
(see stoat_list_identities).
"""

ChannelArg = Annotated[
    str, Field(description="Channel ID or name, e.g. '#market-research' or '01J...'.")
]
IdentityArg = Annotated[
    str | None,
    Field(description="Optional named bot identity to act as; defaults to the service bot."),
]

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def create_server(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastMCP:
    """Build the MCP server. ``transport`` lets tests point it at a mock API."""
    settings = settings or Settings.from_env()
    state: dict[str, StoatClient] = {}

    def stoat() -> StoatClient:
        if "client" not in state:
            state["client"] = StoatClient(settings, transport=transport)
        return state["client"]

    async def resolve(channel: str, identity: str | None = None) -> str:
        try:
            return await stoat().resolve_channel_id(channel, identity)
        except (StoatError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    mcp = FastMCP("stoat-mcp", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=WRITE)
    async def stoat_post_message(
        channel: ChannelArg,
        content: Annotated[str, Field(description="Message text (Markdown supported).")],
        reply_to: Annotated[
            str | None, Field(description="Optional message ID to reply to.")
        ] = None,
        identity: IdentityArg = None,
    ) -> dict[str, Any]:
        """Post a message, or a threaded reply, to a Stoat channel.

        Content over the per-message limit is split into sequential messages on
        paragraph/line boundaries; very long content is uploaded as a Markdown
        attachment instead.
        """
        if not content.strip():
            raise ToolError("content must not be empty")
        channel_id = await resolve(channel, identity)
        # Only split as far as needed to decide between messages and attachment.
        chunks = split_message(content, settings.message_limit, settings.max_chunks)
        try:
            if len(chunks) > settings.max_chunks:
                file_id = await stoat().upload_attachment(
                    "message.md", content.encode("utf-8"), identity=identity
                )
                summary = (
                    f"Long message ({len(content):,} characters) attached as message.md."
                )
                msg = await stoat().send_message(
                    channel_id,
                    summary,
                    reply_to=reply_to,
                    attachments=[file_id],
                    identity=identity,
                )
                return {
                    "channel_id": channel_id,
                    "message_ids": [msg["_id"]],
                    "delivered_as": "attachment",
                }

            ids: list[str] = []
            for chunk in chunks:
                msg = await stoat().send_message(
                    channel_id,
                    chunk,
                    reply_to=reply_to if not ids else None,
                    identity=identity,
                )
                ids.append(msg["_id"])
        except (StoatError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return {"channel_id": channel_id, "message_ids": ids, "delivered_as": "messages"}

    @mcp.tool(annotations=READ_ONLY)
    async def stoat_read_history(
        channel: ChannelArg,
        limit: Annotated[int, Field(ge=1, le=100, description="Messages to return.")] = 25,
        before: Annotated[
            str | None,
            Field(description="Only return messages older than this message ID (for paging)."),
        ] = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent channel history in chronological order (oldest first),
        with author names resolved."""
        channel_id = await resolve(channel)
        try:
            return await stoat().fetch_messages(channel_id, limit, before)
        except (StoatError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def stoat_list_channels(
        server_id: Annotated[
            str | None,
            Field(description="Limit to one server ID; defaults to all configured servers."),
        ] = None,
    ) -> list[dict[str, Any]]:
        """List channels with their names, types, topics and IDs."""
        try:
            channels = await stoat().list_channels(server_id)
        except (StoatError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return [
            {
                "channel_id": c.get("_id"),
                "name": c.get("name"),
                "type": c.get("channel_type"),
                "topic": c.get("description"),
                "server_id": c.get("server"),
                "server_name": c.get("_server_name"),
            }
            for c in channels
        ]

    @mcp.tool(annotations=READ_ONLY)
    async def stoat_search_messages(
        query: Annotated[
            str, Field(description="Words that must all appear in the message (case-insensitive).")
        ],
        channel: Annotated[
            str | None,
            Field(description="Channel ID or name; omit to search all text channels."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum results.")] = 20,
    ) -> list[dict[str, Any]]:
        """Search past messages for discussion topics or earlier deliverables.

        Results are newest first. Stoat does not allow bots to use its search
        API, so only the most recent messages of each channel are scanned
        (STOAT_SEARCH_SCAN_LIMIT, default 1,000 per channel).
        """
        if not query.strip():
            raise ToolError("query must not be empty")
        try:
            if channel:
                channel_ids = [await resolve(channel)]
            else:
                channel_ids = [
                    c["_id"]
                    for c in await stoat().list_channels()
                    if c.get("channel_type", "TextChannel") in TEXT_CHANNEL_TYPES
                ]
            results: list[dict[str, Any]] = []
            for channel_id in channel_ids:
                results.extend(await stoat().search_messages(channel_id, query, limit))
        except (StoatError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        if len(channel_ids) > 1:
            # Merge per-channel results newest first; ULIDs sort by time.
            results.sort(key=lambda m: m.get("message_id") or "", reverse=True)
        return results[:limit]

    @mcp.tool(annotations=WRITE)
    async def stoat_upload_file(
        channel: ChannelArg,
        filename: Annotated[str, Field(description="File name shown in chat, e.g. 'report.md'.")],
        file_bytes_base64: Annotated[str, Field(description="Base64-encoded file contents.")],
        content: Annotated[
            str | None, Field(description="Optional message text to accompany the file.")
        ] = None,
        identity: IdentityArg = None,
    ) -> dict[str, Any]:
        """Upload a file to Autumn and post it as an attachment in a channel."""
        try:
            data = base64.b64decode(file_bytes_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ToolError(f"file_bytes_base64 is not valid base64: {exc}") from exc
        if not data:
            raise ToolError("file is empty")
        if len(data) > settings.max_upload_bytes:
            raise ToolError(
                f"file is {len(data):,} bytes; the limit is {settings.max_upload_bytes:,}"
            )
        if content and len(content) > settings.message_limit:
            raise ToolError(
                f"content must be at most {settings.message_limit} characters"
            )
        channel_id = await resolve(channel, identity)
        try:
            file_id = await stoat().upload_attachment(filename, data, identity=identity)
            msg = await stoat().send_message(
                channel_id, content, attachments=[file_id], identity=identity
            )
        except (StoatError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return {"channel_id": channel_id, "message_id": msg["_id"], "attachment_id": file_id}

    @mcp.tool(annotations=READ_ONLY)
    async def stoat_list_identities() -> list[str]:
        """List the named bot identities that can be passed as `identity`."""
        return sorted(settings.identities)

    return mcp
