#!/usr/bin/env python3
"""End-to-end transport test against a running graphical Houdini session.

    python tests/integration/bridge_e2e.py --port 18100

Drives the real Houdini hwebserver through the MCP server's own async
``HoudiniBridge``. The target must be a graphical session: Hython has no UI
event loop, so its hwebserver can answer a startup health probe but cannot
reliably service subsequent requests. The Windows full-validation script
launches an isolated GUI target before invoking this test.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python"))


async def _exercise(host: str, port: int) -> None:
    from fxhoudinimcp.bridge import HoudiniBridge
    from fxhoudinimcp.errors import FXHoudiniError

    bridge = HoudiniBridge(host=host, port=port)
    created_path: str | None = None
    try:
        health = await bridge.health_check()
        assert health.get("status") == "ok", health
        assert health.get("ui_available") is True, health
        print(f"[e2e] health: Houdini {health.get('houdini_version')}")

        info = await bridge.execute("scene.get_scene_info")
        assert "houdini_version" in str(info), info
        print("[e2e] scene.get_scene_info over HTTP: ok")

        created = await bridge.execute(
            "nodes.create_node",
            {"parent_path": "/obj", "node_type": "geo", "name": "via_http"},
        )
        created_path = created["node_path"]
        assert created_path == "/obj/via_http", created
        listed = await bridge.execute(
            "nodes.find_nodes", {"pattern": "via_http"}
        )
        assert listed["count"] == 1, listed
        print("[e2e] node created and found through the bridge: ok")

        big = await bridge.execute(
            "nodes.list_node_types", {"context": "Sop", "limit": 5000}
        )
        assert big["total_count"] > 200
        print(f"[e2e] large payload ({big['total_count']} types) serialized: ok")

        try:
            await bridge.execute(
                "nodes.create_node",
                {"parent_path": "/obj", "node_type": "not_a_real_type"},
            )
        except FXHoudiniError as exc:
            assert "not_a_real_type" in str(exc), exc
            print("[e2e] structured error surfaced through the bridge: ok")
        else:
            raise AssertionError("bad node type did not raise through bridge")

        try:
            await bridge.execute("no.such.command")
        except FXHoudiniError as exc:
            print(f"[e2e] unknown command rejected: ok ({type(exc).__name__})")
        else:
            raise AssertionError("unknown command did not raise")
    finally:
        if created_path:
            await bridge.execute("nodes.delete_node", {"node_path": created_path})
        await bridge.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    print(f"[e2e] exercising GUI Houdini bridge at {args.host}:{args.port}")
    asyncio.run(_exercise(args.host, args.port))
    print("[e2e] ALL TRANSPORT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
