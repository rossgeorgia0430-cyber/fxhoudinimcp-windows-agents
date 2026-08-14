"""MCP tool definitions for Houdini viewport and UI operations.

Provides tools for inspecting and controlling Houdini's viewport panes,
display modes, camera assignment, screenshot capture, network editor
navigation, and error node discovery.
"""

from __future__ import annotations

# Built-in
from typing import Any

# Third-party
from mcp.server.fastmcp import Context
from mcp.types import ImageContent, TextContent

# Internal
from fxhoudinimcp.server import mcp, _get_bridge
from fxhoudinimcp.tools import result_with_image


@mcp.tool()
async def list_panes(ctx: Context) -> dict:
    """List all visible pane tabs in the Houdini UI."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("viewport.list_panes", {})


@mcp.tool()
async def get_viewport_info(
    ctx: Context,
    pane_name: str | None = None,
) -> dict:
    """Get viewport settings for a pane tab.

    Args:
        pane_name: Pane tab name.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {}
    if pane_name is not None:
        params["pane_name"] = pane_name
    return await bridge.execute("viewport.get_viewport_info", params)


@mcp.tool()
async def set_viewport_camera(
    ctx: Context,
    camera_path: str,
    pane_name: str | None = None,
) -> dict:
    """Set the viewport to look through a specific camera.

    H22 can look through SOP/COP cameras (`sop/camera`,
    `sop/camerafrompoints`) as well as object cameras. Does not use
    the camera-menu Save View action (removed in H22).

    Args:
        camera_path: Camera node path (object or SOP/COP camera).
        pane_name: Pane tab name.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"camera_path": camera_path}
    if pane_name is not None:
        params["pane_name"] = pane_name
    return await bridge.execute("viewport.set_viewport_camera", params)


@mcp.tool()
async def set_viewport_display(
    ctx: Context,
    display_mode: str,
    pane_name: str | None = None,
) -> dict:
    """Set the viewport shading mode.

    Args:
        display_mode: One of 'wireframe', 'shaded', 'smooth', 'smooth_wire',
            'hidden_line', 'flat', 'flat_wire', 'matcap', 'matcap_wire'.
        pane_name: Pane tab name.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"display_mode": display_mode}
    if pane_name is not None:
        params["pane_name"] = pane_name
    return await bridge.execute("viewport.set_viewport_display", params)


@mcp.tool()
async def set_viewport_renderer(
    ctx: Context,
    renderer: str,
    pane_name: str | None = None,
) -> dict:
    """Set the viewport's Hydra rendering delegate for live preview.

    Use this during lookdev to preview materials and lighting directly in the
    viewport instead of writing full renders to disk.

    Args:
        renderer: Renderer name — "Houdini VK" (H22 default), "Storm",
            "Karma CPU", "Karma XPU". Pre-22 viewports still expose
            "Houdini GL". Case-insensitive partial match.
        pane_name: Pane tab name.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"renderer": renderer}
    if pane_name is not None:
        params["pane_name"] = pane_name
    return await bridge.execute("viewport.set_viewport_renderer", params)


@mcp.tool()
async def frame_selection(
    ctx: Context,
    pane_name: str | None = None,
) -> dict:
    """Frame the current selection in the viewport.

    Args:
        pane_name: Pane tab name.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {}
    if pane_name is not None:
        params["pane_name"] = pane_name
    return await bridge.execute("viewport.frame_selection", params)


@mcp.tool()
async def frame_all(
    ctx: Context,
    pane_name: str | None = None,
) -> dict:
    """Frame all geometry in the viewport.

    Args:
        pane_name: Pane tab name.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {}
    if pane_name is not None:
        params["pane_name"] = pane_name
    return await bridge.execute("viewport.frame_all", params)


@mcp.tool()
async def set_viewport_direction(
    ctx: Context,
    direction: str,
    pane_name: str | None = None,
) -> dict:
    """Set the viewport to a standard viewing direction.

    Args:
        direction: "front", "back", "top", "bottom", "left", "right", or "perspective".
        pane_name: Pane tab name.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"direction": direction}
    if pane_name is not None:
        params["pane_name"] = pane_name
    return await bridge.execute("viewport.set_viewport_direction", params)


@mcp.tool()
async def capture_screenshot(
    ctx: Context,
    output_path: str,
    pane_name: str | None = None,
) -> list[TextContent | ImageContent]:
    """Capture a screenshot of the viewport or a specific pane tab.

    Screenshots consume significant context tokens. Only take one when visual
    confirmation is genuinely needed — prefer get_geometry_info, get_node_info,
    or get_scene_summary for most inspection tasks.

    Args:
        output_path: Image file path.
        pane_name: Pane tab name.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"output_path": output_path}
    if pane_name is not None:
        params["pane_name"] = pane_name
    result = await bridge.execute("viewport.capture_screenshot", params)
    return result_with_image(result)


@mcp.tool()
async def capture_network_editor(
    ctx: Context,
    output_path: str,
    node_path: str | None = None,
) -> list[TextContent | ImageContent]:
    """Capture a screenshot of the network editor.

    Screenshots consume significant context tokens. Only take one when visual
    confirmation of wiring is genuinely needed — prefer get_node_info or
    list_children for inspecting node connections.

    Args:
        output_path: Image file path.
        node_path: Node path to navigate to before capture.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"output_path": output_path}
    if node_path is not None:
        params["node_path"] = node_path
    result = await bridge.execute("viewport.capture_network_editor", params)
    return result_with_image(result)


@mcp.tool()
async def flipbook(
    ctx: Context,
    output_path: str | None = None,
    start_frame: int | None = None,
    end_frame: int | None = None,
    camera_path: str | None = None,
    resolution: list[int] | None = None,
    frame_increment: int = 1,
    open_in_mplay: bool | None = None,
    pane_name: str | None = None,
) -> dict:
    """Record a viewport flipbook (playblast) over a frame range.

    Cooks every frame once and plays it back at speed — the practical fix for a
    scene too heavy to scrub live. With no output_path the flipbook streams to
    MPlay (in-memory, fastest); give an output_path to also write it to disk.
    Unlike capture_screenshot (one frame), this records a whole range and
    returns only metadata, not image bytes. Long ranges cook every frame, so a
    huge range may exceed the command timeout — lower the resolution, raise
    frame_increment, or shorten the range for a quick rough preview.

    Args:
        output_path: Destination. Empty → MPlay only. An image sequence needs a
            frame token (e.g. ".../preview.$F4.jpg"); ".../preview.mp4" writes video.
        start_frame: First frame. Defaults to the playbar start.
        end_frame: Last frame. Defaults to the playbar end.
        camera_path: Camera object to look through first (e.g. "/obj/cam1") for
            true shot framing. Omitted → current viewport view.
        resolution: [width, height] in pixels. Omitted → current viewport size.
        frame_increment: Frame step; >1 skips frames for a faster rough preview.
        open_in_mplay: Force MPlay on/off. Defaults on when output_path is empty.
        pane_name: Scene Viewer pane tab to capture. Omitted → first found.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"frame_increment": frame_increment}
    if output_path is not None:
        params["output_path"] = output_path
    if start_frame is not None:
        params["start_frame"] = start_frame
    if end_frame is not None:
        params["end_frame"] = end_frame
    if camera_path is not None:
        params["camera_path"] = camera_path
    if resolution is not None:
        params["resolution"] = resolution
    if open_in_mplay is not None:
        params["open_in_mplay"] = open_in_mplay
    if pane_name is not None:
        params["pane_name"] = pane_name
    return await bridge.execute("viewport.flipbook", params)


@mcp.tool()
async def set_current_network(
    ctx: Context,
    network_path: str,
) -> dict:
    """Navigate the network editor to a specific network path.

    Args:
        network_path: Network path to navigate to.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "viewport.set_current_network",
        {"network_path": network_path},
    )


@mcp.tool()
async def find_error_nodes(
    ctx: Context,
    root_path: str = "/",
) -> dict:
    """Find all nodes with errors or warnings in the scene.

    Args:
        root_path: Root node path to search from.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "viewport.find_error_nodes",
        {"root_path": root_path},
    )


@mcp.tool()
async def log_status(
    ctx: Context,
    message: str,
    severity: str = "message",
) -> dict:
    """Display a status message in Houdini's status bar.

    Call this at the START of every major step so the user can follow
    along in real time without having to inspect tool call logs.
    Examples: "Creating base geometry...", "Wiring SOP chain...",
    "Setting up pyro simulation...", "Assigning materials...".

    Args:
        message: Status message to display (keep it short and human-readable).
        severity: "message" (default), "important", "warning", or "error".
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "viewport.log_status",
        {"message": message, "severity": severity},
    )
