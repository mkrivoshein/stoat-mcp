FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 stoat

USER stoat

# Network transport by default inside the container; the port should only be
# reachable on the private (Tailscale / internal Docker) network.
ENV STOAT_MCP_TRANSPORT=http \
    STOAT_MCP_HOST=0.0.0.0 \
    STOAT_MCP_PORT=8080
EXPOSE 8080

CMD ["stoat-mcp"]
