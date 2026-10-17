"""Run the mock as a standalone HTTP service: ``python -m mock_stoat``."""

from __future__ import annotations

import os

import uvicorn

from .app import MockStoat, create_app, load_seed


def main() -> None:
    app = create_app(MockStoat(load_seed()))
    uvicorn.run(
        app,
        host=os.environ.get("MOCK_STOAT_HOST", "127.0.0.1"),
        port=int(os.environ.get("MOCK_STOAT_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
