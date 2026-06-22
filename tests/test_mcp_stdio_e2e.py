"""Production-shape stdio MCP handshake test.

The HTTP bridge tests prove the Houdini hop, but Codex starts this project as
an stdio MCP subprocess.  Exercise the actual initialize/list/call protocol
so a packaging or registration regression cannot hide behind direct Python
function tests.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


REPO_ROOT = Path(__file__).resolve().parents[1]


async def _exercise_stdio_server() -> None:
    env = os.environ.copy()
    # No Houdini service is needed for initialization. This deliberately
    # unreachable port proves that the discovery surface remains available
    # before a GUI session is connected.
    env.update(
        {
            "HOUDINI_HOST": "127.0.0.1",
            "HOUDINI_PORT": "65531",
            "FXHOUDINIMCP_TOOL_PROFILE": "full",
        }
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fxhoudinimcp"],
        env=env,
        cwd=REPO_ROOT,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "FXHoudini"

            tools = await session.list_tools()
            assert len(tools.tools) == 180
            assert {tool.name for tool in tools.tools} >= {
                "build_network",
                "get_node_card",
                "capture_screenshot",
                "setup_pyro_sim",
            }

            templates = await session.list_resource_templates()
            prompts = await session.list_prompts()
            # All eight Houdini resources are URI templates because every
            # read is live session state; there are no static resources.
            assert len((await session.list_resources()).resources) == 0
            assert len(templates.resourceTemplates) == 8
            assert len(prompts.prompts) == 6

            status = await session.call_tool("get_houdini_connection_status", {})
            assert status.isError is False
            assert status.content
            payload = json.loads(status.content[0].text)
            assert payload["connected"] is False


def test_stdio_initialize_and_full_discovery_surface():
    asyncio.run(_exercise_stdio_server())
