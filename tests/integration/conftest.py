"""Fixtures for integration tests that require a live Houdini.

These tests run under hython, Houdini's standalone Python interpreter:

    tests/run_integration.ps1

The directory is ignored automatically when the real ``hou`` module is
unavailable (plain ``pytest`` runs, or unit-test runs where ``hou`` is
mocked into ``sys.modules``).
"""

from __future__ import annotations

# Built-in
import os
import sys
from collections import defaultdict

# Third-party
import pytest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "..", "houdini", "scripts", "python"
    ),
)

try:
    import hou

    _REAL_HOU = isinstance(hou.applicationVersionString(), str)
except Exception:
    _REAL_HOU = False

if not _REAL_HOU:
    collect_ignore_glob = ["*"]
else:
    import fxhoudinimcp_server.dispatcher as dispatcher
    import fxhoudinimcp_server.handlers  # noqa: F401  (registers all handlers)

    # hython is single-threaded with no UI event loop; run handlers
    # directly on this thread instead of marshalling via hdefereval.
    dispatcher.HAS_HDEFEREVAL = False

# (command, milliseconds) for every dispatched call, across the session.
_OP_TIMINGS: list[tuple[str, float]] = []


@pytest.fixture(autouse=True)
def fresh_scene():
    """Start every test from an empty scene."""
    hou.hipFile.clear(suppress_save_prompt=True)
    yield


@pytest.fixture
def call():
    """Dispatch a command exactly as the HTTP bridge would.

    Returns the handler's data dict on success. With ``expect_error=True``,
    asserts the command failed and returns the error dict instead.
    """

    def _call(command: str, expect_error: bool = False, **params):
        result = dispatcher.dispatch(command, params)
        _OP_TIMINGS.append((command, result.get("timing_ms", 0.0)))
        if expect_error:
            assert result["status"] == "error", (
                f"{command} unexpectedly succeeded: {result.get('data')}"
            )
            return result["error"]
        assert result["status"] == "success", (
            f"{command} failed: {result.get('error', {}).get('message')}"
        )
        return result["data"]

    return _call


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print per-command timing aggregates after the run."""
    if not _OP_TIMINGS:
        return
    stats: dict[str, list[float]] = defaultdict(list)
    for command, ms in _OP_TIMINGS:
        stats[command].append(ms)
    rows = sorted(
        ((max(v), sum(v) / len(v), len(v), k) for k, v in stats.items()),
        reverse=True,
    )
    terminalreporter.write_sep("=", "handler timings (ms)")
    terminalreporter.write_line(
        f"{'command':<42} {'calls':>5} {'mean':>9} {'max':>9}"
    )
    for mx, mean, count, command in rows:
        terminalreporter.write_line(
            f"{command:<42} {count:>5} {mean:>9.1f} {mx:>9.1f}"
        )
