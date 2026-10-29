# stoat-mcp

An MCP server for access to a private Stoat (Revolt) instance.

It gives agent runtimes (Claude Desktop, LangGraph, CrewAI, AutoGen, …) a
standard tool interface to read channel history, search past discussion, list
channels and post results back into chat. It is meant to run inside a private
network (for example a Tailscale sidecar) next to the Stoat API, so bot tokens
and the API never leave the mesh.

## Tools

| Tool | Parameters | Description |
|---|---|---|
| `stoat_post_message` | `channel`, `content`, `reply_to?`, `identity?`, `message_type?`, `metadata?` | Posts a message or reply, optionally with a [metadata header](#structured-message-metadata). Content over the length limit is split on paragraph/line boundaries (code fences are closed and reopened across chunks); very long content is uploaded as a `message.md` attachment instead. |
| `stoat_read_history` | `channel`, `limit=25`, `before?`, `message_type?`, `metadata?` | Recent history in chronological order with author names resolved (nickname → display name → username, masquerade honoured). With a filter, returns the latest matching messages. |
| `stoat_list_channels` | `server_id?` | Channels with name, type, topic, ID and server. |
| `stoat_search_messages` | `query?`, `channel?`, `limit=20`, `message_type?`, `metadata?` | Finds messages containing every word of `query` and matching the metadata filter (at least one is required), in one channel or across all text channels of the configured servers. Stoat's search API refuses bot accounts, so this scans each channel's most recent `STOAT_SEARCH_SCAN_LIMIT` messages. |
| `stoat_upload_file` | `channel`, `filename`, `file_bytes_base64`, `content?`, `identity?` | Uploads to Autumn and posts the file as an attachment. |
| `stoat_list_identities` | — | Names of the bot identities that can be passed as `identity`. |

Everywhere a `channel` is accepted it may be a raw channel ID or a name
(`#market-research` or `market-research`). Names are resolved against the
channels of the servers in `STOAT_SERVER_IDS` and cached (default 10 minutes);
ambiguous names return an error listing the matching IDs. When a tool is
called with an `identity`, names are resolved among the channels that bot can
see.

### Structured message metadata

Agents and orchestrators can tag messages with a machine-readable header on
the first line, so tasks and results can be routed and filtered:

```
[TASK] id:42 | priority:high
Research the EU market for product X.

[RESULT] id:42 | status:complete
Report attached.
```

Format:

- **Type** — `[TYPE]` at the very start of the message: a letter followed by
  up to 31 letters, digits, `_` or `-`. Any type is allowed; `TASK` and
  `RESULT` are the conventional ones. Case-insensitive, normalised to upper
  case.
- **Fields** (optional) — after the type and a space, `key:value` pairs
  separated by `|`. Keys start with a letter and contain letters, digits, `_`
  or `-` (case-insensitive, normalised to lower case). Values are trimmed and
  may contain `:`; write a literal `|` as `\|` and `\` as `\\`. Values are
  single-line. Suggested fields: `id` (required by convention for `TASK` and
  `RESULT`, to pair them), `priority` (`low`/`normal`/`high`) and `status`
  (`complete`/`partial`/`blocked`/`failed`).
- **Body** — everything after the header line.

Parsing is tolerant: a message whose first line is not a header is plain
text, and header segments that are not `key:value` pairs are ignored.

- To post with a header, pass `message_type` and optionally `metadata`
  (e.g. `{"id": "42", "status": "complete"}`) to `stoat_post_message`; the
  header is added and escaped for you. Writing the header into `content`
  by hand works too.
- Messages returned by `stoat_read_history` and `stoat_search_messages`
  include a parsed `metadata` object (`{"type": "RESULT", "fields": {...}}`)
  when they have a header.
- Both tools accept `message_type` and `metadata` as filters: a message
  matches when its type is equal and it has every given field with an equal
  value (case-insensitive). Filtering scans the most recent
  `STOAT_SEARCH_SCAN_LIMIT` messages of each channel.

### Multiple bot identities

Sub-agents can speak under their own bot accounts. Configure them by name,
and agents pass the name as `identity`:

```
STOAT_BOT_IDENTITIES="researcher=<token>,reviewer=<token>"
```

Tokens never pass through tool arguments, so they do not end up in model
context or client logs.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `STOAT_API_URL` | `http://stoat-api:8000` | Stoat (Revolt) API base URL. |
| `STOAT_BOT_TOKEN` | — | Default bot token (`x-bot-token`). Required unless every call names an identity. |
| `STOAT_BOT_IDENTITIES` | — | Extra identities, `name=token,…`. |
| `STOAT_SERVER_IDS` | — | Comma-separated server IDs used for channel listing, name resolution and global search. |
| `STOAT_AUTUMN_URL` | discovered from `GET /` | Autumn file server URL. |
| `STOAT_MESSAGE_LIMIT` | `2000` | Max characters per message. |
| `STOAT_MAX_CHUNKS` | `8` | Above this many chunks, content is sent as an attachment. |
| `STOAT_MAX_UPLOAD_BYTES` | `20971520` | Upload size cap (applies to `stoat_upload_file` and to long messages sent as attachments). |
| `STOAT_SEARCH_SCAN_LIMIT` | `1000` | Recent messages scanned per channel by `stoat_search_messages` and filtered `stoat_read_history`. |
| `STOAT_CACHE_TTL` | `600` | Seconds to cache channels and users. |
| `STOAT_RATE_LIMIT_PER_SECOND` / `STOAT_RATE_LIMIT_BURST` | `5` / `10` | Client-side token bucket for API calls (`0` disables). HTTP 429 responses are retried using `retry_after`. |
| `STOAT_REQUEST_TIMEOUT` | `15` | HTTP timeout in seconds. |
| `STOAT_MCP_TRANSPORT` | `stdio` | `stdio`, `http` (Streamable HTTP at `/mcp`) or `sse` (legacy, at `/sse`). |
| `STOAT_MCP_HOST` / `STOAT_MCP_PORT` | `127.0.0.1` / `8080` | Bind address for network transports. |

The bot needs to be a member of the configured servers with permission to view
channels, read message history and send messages (plus upload files for
attachments).

## Running

Locally over stdio:

```sh
pip install .
STOAT_API_URL=http://localhost:8000 STOAT_BOT_TOKEN=... STOAT_SERVER_IDS=... stoat-mcp
```

With Docker (defaults to Streamable HTTP on port 8080): see
[`deploy/docker-compose.example.yml`](deploy/docker-compose.example.yml) for a
service that shares a Tailscale sidecar's network namespace.

> The network transports have no authentication of their own. Only expose the
> port on a private network (tailnet / internal Docker network).

### Client configuration

Streamable HTTP over the tailnet:

```json
{
  "mcpServers": {
    "stoat-chat": { "url": "http://mongo-node-1.your-tailnet.ts.net:8080/mcp" }
  }
}
```

Use `STOAT_MCP_TRANSPORT=sse` and `…:8080/sse` for clients that only speak SSE.

stdio on the same host:

```json
{
  "mcpServers": {
    "stoat-chat": {
      "command": "stoat-mcp",
      "env": { "STOAT_API_URL": "http://localhost:8000", "STOAT_BOT_TOKEN": "...", "STOAT_SERVER_IDS": "..." }
    }
  }
}
```

## Development

```sh
pip install -e ".[dev]"
pytest
```

### Mock Stoat

[`mock_stoat/`](mock_stoat/) is a stateful mock of the parts of the Stoat API
and Autumn that this server uses. Like the real API, it authenticates every
request by `x-bot-token`, authors messages as the bot that owns the token,
only shows servers a bot is a member of, enforces the 2,000-character limit,
refuses to attach a file uploaded by a different bot, and rejects bots on the
search route with `IsBot`. It can also inject
`429` responses. The seed data (servers, channels, bots and their tokens) is
in `mock_stoat/app.py`; pass a JSON file of the same shape in
`MOCK_STOAT_SEED` to replace it.

The tests use it in three ways:

- **In-process** (`tests/test_server.py`, `tests/test_identities.py`): the
  MCP server's HTTP calls go straight to the mock app through
  `httpx.ASGITransport`.
- **Real processes** (`tests/test_e2e.py`): the mock and `stoat-mcp` start as
  subprocesses, and MCP clients connect over Streamable HTTP.
- **Docker**: the same e2e test runs against containers:

  ```sh
  docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
  ```

  Without the `tests` service, the stack is a local sandbox: point an MCP
  client at `http://localhost:8080/mcp`.

`tests/test_identities.py` checks that agents posting at the same time under
different identities are never mixed up. It looks at what the mock recorded
(which token reached the API, who authored each message, who owns each file),
not at what the tool reported.

The mock covers only what this server calls. Before deploying, run a smoke
test against a real (staging) Stoat instance by pointing `STOAT_API_URL` at it.
