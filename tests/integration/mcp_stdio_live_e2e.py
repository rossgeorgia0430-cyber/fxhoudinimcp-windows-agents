#!/usr/bin/env python3
"""Exercise the stdio MCP launcher against a running Houdini GUI.

    python tests/integration/mcp_stdio_live_e2e.py --port 18100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "windows" / "start-fxhoudinimcp.ps1"


async def _exercise(port: int) -> None:
    env = os.environ.copy()
    env.update({"HOUDINI_HOST": "127.0.0.1", "HOUDINI_PORT": str(port)})
    params = StdioServerParameters(
        command="powershell.exe",
        args=["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LAUNCHER)],
        env=env,
        cwd=REPO_ROOT,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert len(tools.tools) == 79, len(tools.tools)
            assert tool_names >= {
                "get_houdini_connection_status",
                "get_scene_info",
                "build_network",
                "get_node_card",
                "capture_screenshot",
            }

            status = await session.call_tool("get_houdini_connection_status", {})
            assert status.isError is False
            status_payload = json.loads(status.content[0].text)
            assert status_payload["connected"] is True

            scene = await session.call_tool("get_scene_info", {})
            assert scene.isError is False
            payload = json.loads(scene.content[0].text)
            assert "houdini_version" in payload, payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(_exercise(args.port))
    print("[stdio-e2e] stdio launcher, MCP discovery, and live scene call: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
