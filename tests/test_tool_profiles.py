"""Tests for real FastMCP tool profile filtering."""

from __future__ import annotations

# Built-in
import json
import os
import subprocess
import sys
from pathlib import Path

# Third-party
import pytest

# Internal
from fxhoudinimcp.tool_profiles import (
    DEFAULT_PROFILE,
    PROFILE_ENV_VAR,
    profile_names,
    resolve_tool_profile,
)

ROOT = Path(__file__).resolve().parents[1]


def _profile_snapshot(profile: str | None) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "python")
    env.pop(PROFILE_ENV_VAR, None)
    env.pop("HOUDINI_MCP_TOOL_PROFILE", None)
    if profile is not None:
        env[PROFILE_ENV_VAR] = profile
    code = """
import asyncio
import json
import fxhoudinimcp.tools
from fxhoudinimcp.server import mcp
from fxhoudinimcp.tool_profiles import get_active_tool_profile

tools = asyncio.run(mcp.list_tools())
print(json.dumps({
    "status": get_active_tool_profile(),
    "tools": sorted(tool.name for tool in tools),
    "hidden_callable": mcp._tool_manager.get_tool("setup_pyro_sim") is not None,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_profile_names_are_stable():
    assert profile_names() == (
        "core",
        "modeling",
        "simulation",
        "usd-render",
        "full",
    )
    assert DEFAULT_PROFILE == "core"


def test_profile_resolution_and_alias():
    assert resolve_tool_profile({}) == ("core", "core", None)
    assert resolve_tool_profile({"HOUDINI_MCP_TOOL_PROFILE": "simulation"}) == (
        "simulation",
        "simulation",
        None,
    )
    active, requested, reason = resolve_tool_profile({PROFILE_ENV_VAR: "bogus"})
    assert (active, requested) == ("core", "bogus")
    assert "falling back" in reason


@pytest.mark.parametrize(
    ("profile", "expected_count"),
    [
        (None, 100),
        ("core", 100),
        ("modeling", 132),
        ("simulation", 139),
        ("usd-render", 155),
        ("full", 201),
    ],
)
def test_profiles_filter_the_actual_fastmcp_registry(profile, expected_count):
    snapshot = _profile_snapshot(profile)
    status = snapshot["status"]
    expected_name = profile or "core"
    assert status["name"] == expected_name
    assert status["tool_count"] == expected_count
    assert len(snapshot["tools"]) == expected_count
    assert "get_houdini_connection_status" in snapshot["tools"]
    assert ("setup_pyro_sim" in snapshot["tools"]) == (
        expected_name in {"simulation", "full"}
    )
    assert snapshot["hidden_callable"] == (
        expected_name in {"simulation", "full"}
    )


def test_invalid_profile_falls_back_to_real_core_surface():
    snapshot = _profile_snapshot("not-a-profile")
    assert snapshot["status"]["name"] == "core"
    assert snapshot["status"]["requested"] == "not-a-profile"
    assert snapshot["status"]["tool_count"] == 100
    assert "fallback_reason" in snapshot["status"]
