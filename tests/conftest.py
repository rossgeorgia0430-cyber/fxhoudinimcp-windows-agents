"""Shared fixtures for fxhoudinimcp tests."""

from __future__ import annotations

# Built-in
import os
from unittest.mock import AsyncMock, MagicMock

# Third-party
import pytest

# Most contract tests intentionally validate the complete wrapper catalog.
# Production defaults to the smaller core profile; profile-specific tests use
# isolated subprocesses so import-time FastMCP registration stays deterministic.
os.environ.setdefault("FXHOUDINIMCP_TOOL_PROFILE", "full")


@pytest.fixture
def mock_bridge():
    """A mocked HoudiniBridge whose execute() returns a success dict."""
    bridge = AsyncMock()
    bridge.execute = AsyncMock(return_value={"executed": True})
    bridge.health_check = AsyncMock(return_value={"status": "ok", "houdini_version": "99.0.0-test"})
    return bridge


@pytest.fixture
def mock_ctx(mock_bridge):
    """A mocked MCP Context wired to mock_bridge."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {"bridge": mock_bridge}
    return ctx
