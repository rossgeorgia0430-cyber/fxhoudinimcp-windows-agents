"""Tests for the command dispatcher."""

from __future__ import annotations

# Built-in
import os
import sys
from unittest.mock import MagicMock

# Mock Houdini modules before importing dispatcher
sys.modules.setdefault("hou", MagicMock())
sys.modules.setdefault("hdefereval", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "houdini", "scripts", "python"))

from fxhoudinimcp_server.dispatcher import (  # noqa: E402
    _handler_registry,
    dispatch,
    list_commands,
    register_handler,
)

# Force hython fallback (no hdefereval threading)
import fxhoudinimcp_server.dispatcher as _disp  # noqa: E402
_disp.HAS_HDEFEREVAL = False


class TestHandlerRegistry:
    def setup_method(self):
        """Clear registry before each test."""
        _handler_registry.clear()

    def test_register_and_list(self):
        register_handler("b.cmd", lambda: None)
        register_handler("a.cmd", lambda: None)
        register_handler("c.cmd", lambda: None)
        assert list_commands() == ["a.cmd", "b.cmd", "c.cmd"]

    def test_register_overwrites(self):
        fn1 = lambda: "first"
        fn2 = lambda: "second"
        register_handler("test.cmd", fn1)
        register_handler("test.cmd", fn2)
        assert _handler_registry["test.cmd"] is fn2

    def test_list_empty(self):
        assert list_commands() == []


class TestDispatch:
    def setup_method(self):
        _handler_registry.clear()

    def test_success(self):
        def handler(name="world"):
            return {"greeting": f"hello {name}"}

        register_handler("test.greet", handler)
        result = dispatch("test.greet", {"name": "claude"})

        assert result["status"] == "success"
        assert result["data"] == {"greeting": "hello claude"}
        assert "timing_ms" in result
        assert isinstance(result["timing_ms"], float)

    def test_unknown_command(self):
        result = dispatch("nonexistent.command", {})
        assert result["status"] == "error"
        assert result["error"]["code"] == "UNKNOWN_COMMAND"
        # The full command list is no longer dumped (it bloated every error);
        # a count plus did-you-mean suggestions replace it.
        assert "available_commands" not in result["error"]
        assert "command_count" in result["error"]

    def test_handler_exception(self):
        def bad_handler(**_):
            raise ValueError("something went wrong")

        register_handler("test.fail", bad_handler)
        result = dispatch("test.fail", {})

        assert result["status"] == "error"
        assert result["error"]["code"] == "ValueError"
        assert "something went wrong" in result["error"]["message"]
        assert result["error"]["retryable"] is False
        assert "traceback" not in result["error"]

    def test_timing_always_present(self):
        register_handler("test.noop", lambda **_: {})
        result = dispatch("test.noop", {})
        assert "timing_ms" in result
        assert result["timing_ms"] >= 0

    def test_handler_with_no_params(self):
        register_handler("test.simple", lambda **_: {"ok": True})
        result = dispatch("test.simple", {})
        assert result["status"] == "success"
        assert result["data"]["ok"] is True
