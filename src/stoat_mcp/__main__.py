"""Entry point: ``python -m stoat_mcp`` or the ``stoat-mcp`` script."""

from __future__ import annotations

from .config import Settings
from .server import create_server


def main() -> None:
    settings = Settings.from_env()
    mcp = create_server(settings)
    if settings.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=settings.transport, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
