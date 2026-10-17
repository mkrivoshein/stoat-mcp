"""A stateful mock of the Stoat (Revolt) REST API and Autumn, for testing."""

from .app import DEFAULT_SEED, MockStoat, create_app

__all__ = ["DEFAULT_SEED", "MockStoat", "create_app"]
