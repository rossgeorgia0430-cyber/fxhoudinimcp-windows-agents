"""MCP tool wrappers for Houdini scene operations.

Each tool delegates to the corresponding handler running inside Houdini
via the HTTP bridge.
"""

from __future__ import annotations

# Built-in
from typing import Optional

# Third-party
from mcp.server.fastmcp import Context

# Internal
from fxhoudinimcp.errors import ConnectionError as HoudiniConnectionError
from fxhoudinimcp.server import _get_bridge, mcp
from fxhoudinimcp.tool_profiles import get_active_tool_profile


@mcp.tool()
async def get_houdini_connection_status(ctx: Context) -> dict:
    """Check the Codex-to-Houdini bridge without raising on disconnect.

    Returns structured connection diagnostics, including the configured bridge
    URL and Houdini health payload when reachable. Use this before live viewport
    workflows when Houdini may have restarted or its hwebserver may not be
    running.
    """
    bridge = _get_bridge(ctx)
    profile = get_active_tool_profile()
    try:
        health = await bridge.health_check()
    except HoudiniConnectionError as exc:
        return {
            "connected": False,
            "base_url": bridge.base_url,
            "mcp_tool_profile": profile,
            "error": str(exc),
            "details": exc.details,
        }
    except Exception as exc:
        return {
            "connected": False,
            "base_url": bridge.base_url,
            "mcp_tool_profile": profile,
            "error": str(exc),
            "details": {"type": type(exc).__name__},
        }

    return {
        "connected": True,
        "base_url": bridge.base_url,
        "mcp_tool_profile": profile,
        "health": health,
    }


@mcp.tool()
async def get_scene_info(ctx: Context) -> dict:
    """Get information about the current Houdini scene."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("scene.get_scene_info")


@mcp.tool()
async def new_scene(ctx: Context, save_current: bool = False) -> dict:
    """Create a new empty Houdini scene.

    Args:
        save_current: Save the current scene before clearing.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "scene.new_scene", {"save_current": save_current}
    )


@mcp.tool()
async def save_scene(ctx: Context, file_path: Optional[str] = None) -> dict:
    """Save the current Houdini scene to disk.

    Args:
        file_path: Destination path; defaults to the current hip file.
    """
    bridge = _get_bridge(ctx)
    params: dict = {}
    if file_path is not None:
        params["file_path"] = file_path
    return await bridge.execute("scene.save_scene", params)


@mcp.tool()
async def load_scene(ctx: Context, file_path: str, merge: bool = False) -> dict:
    """Open or merge a Houdini hip file.

    Args:
        file_path: Path to the hip file to open.
        merge: Merge into the current scene instead of replacing it.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "scene.load_scene",
        {
            "file_path": file_path,
            "merge": merge,
        },
    )


@mcp.tool()
async def import_file(
    ctx: Context,
    file_path: str,
    parent_path: str = "/obj",
    node_name: Optional[str] = None,
) -> dict:
    """Import a geometry, USD, or Alembic file into the scene.

    Args:
        file_path: Path to the file to import.
        parent_path: Network path for the import node.
        node_name: Name for the created node.
    """
    bridge = _get_bridge(ctx)
    params: dict = {
        "file_path": file_path,
        "parent_path": parent_path,
    }
    if node_name is not None:
        params["node_name"] = node_name
    return await bridge.execute("scene.import_file", params)


@mcp.tool()
async def export_file(
    ctx: Context,
    node_path: str,
    file_path: str,
    frame_range: Optional[list[float]] = None,
    timeout: Optional[float] = None,
) -> dict:
    """Export a node's output to a file on disk.

    Exporting a Driver/ROP node triggers a full render, and a frame range cooks
    every frame — both block the main thread. For anything beyond a single
    light frame, pass an explicit `timeout` so the default (120s) doesn't
    abandon the export mid-write.

    Args:
        node_path: Path to the node to export.
        file_path: Destination file path.
        frame_range: Frame range as [start, end] or [start, end, step].
        timeout: Operation budget in seconds. Omit for the default (120s).
    """
    bridge = _get_bridge(ctx)
    params: dict = {
        "node_path": node_path,
        "file_path": file_path,
    }
    if frame_range is not None:
        params["frame_range"] = frame_range
    return await bridge.execute("scene.export_file", params, timeout=timeout)


@mcp.tool()
async def get_context_info(ctx: Context, context: str) -> dict:
    """Get information about a Houdini network context.

    Args:
        context: Context path, e.g. "/obj", "/stage", "/out".
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("scene.get_context_info", {"context": context})


@mcp.tool()
async def set_fps(ctx: Context, fps: float) -> dict:
    """Set the scene's frames-per-second rate.

    Args:
        fps: New frames-per-second value.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("scene.set_fps", {"fps": fps})


@mcp.tool()
async def get_fps(ctx: Context) -> dict:
    """Get the scene's current frames-per-second rate."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("scene.get_fps")


# NOTE: tools/animation.py already registers MCP tools named set_frame /
# get_frame / set_frame_range (backed by the animation.* commands). FastMCP
# keys tools by function name, so these scene variants are exposed under
# distinct scene_* tool names to avoid clobbering the animation tools, while
# still routing to the scene.* handlers requested here.


@mcp.tool(name="scene_set_frame_range")
async def scene_set_frame_range(
    ctx: Context, start: float, end: float, set_playback: bool = True
) -> dict:
    """Set the global frame range, optionally syncing the playback range.

    Args:
        start: First frame of the range.
        end: Last frame of the range.
        set_playback: Also set the playback (playbar) range to match.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "scene.set_frame_range",
        {"start": start, "end": end, "set_playback": set_playback},
    )


@mcp.tool(name="scene_set_frame")
async def scene_set_frame(ctx: Context, frame: float) -> dict:
    """Set the current frame (moves the playbar).

    Args:
        frame: Frame to set as current.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("scene.set_frame", {"frame": frame})


@mcp.tool(name="scene_get_frame")
async def scene_get_frame(ctx: Context) -> dict:
    """Get the current frame."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("scene.get_frame")
