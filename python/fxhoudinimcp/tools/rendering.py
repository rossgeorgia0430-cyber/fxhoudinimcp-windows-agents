"""MCP tool definitions for Houdini rendering operations.

Provides tools for viewport capture, render node management, render execution,
and render progress monitoring.
"""

from __future__ import annotations

# Built-in
from typing import Any

# Third-party
from mcp.server.fastmcp import Context
from mcp.types import ImageContent, TextContent

# Internal
from fxhoudinimcp._types import Value
from fxhoudinimcp.server import mcp, _get_bridge
from fxhoudinimcp.tools import result_with_image


@mcp.tool()
async def render_viewport(
    ctx: Context,
    output_path: str,
    resolution: list[int] | None = None,
    camera: str | None = None,
) -> list[TextContent | ImageContent]:
    """Capture the current 3D viewport to an image file.

    Args:
        output_path: Image file path.
        resolution: [width, height] in pixels.
        camera: Camera node path.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"output_path": output_path}
    if resolution is not None:
        params["resolution"] = resolution
    if camera is not None:
        params["camera"] = camera
    result = await bridge.execute("rendering.render_viewport", params)
    return result_with_image(result)


@mcp.tool()
async def render_quad_view(
    ctx: Context,
    output_path: str,
    resolution: list[int] | None = None,
) -> dict:
    """Capture all four viewport panes to separate images.

    Args:
        output_path: Base image path; viewport names are appended.
        resolution: [width, height] in pixels.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"output_path": output_path}
    if resolution is not None:
        params["resolution"] = resolution
    return await bridge.execute("rendering.render_quad_view", params)


@mcp.tool()
async def list_render_nodes(ctx: Context) -> dict:
    """List all render (ROP/Driver) nodes in the scene."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("rendering.list_render_nodes", {})


@mcp.tool()
async def get_render_settings(ctx: Context, node_path: str) -> dict:
    """Get render settings from a ROP node.

    Args:
        node_path: ROP node path.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "rendering.get_render_settings", {"node_path": node_path}
    )


@mcp.tool()
async def set_render_settings(
    ctx: Context,
    node_path: str,
    settings: dict[str, Value] | None = None,
) -> dict:
    """Set render parameters on a ROP node.

    Args:
        node_path: ROP node path.
        settings: Parameter name-value pairs.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "rendering.set_render_settings",
        {"node_path": node_path, "settings": settings or {}},
    )


@mcp.tool()
async def create_render_node(
    ctx: Context,
    renderer: str,
    name: str | None = None,
    camera: str | None = None,
    output_path: str | None = None,
) -> dict:
    """Create a new render (ROP) node in /out.

    Args:
        renderer: Renderer type ('karma', 'opengl', 'mantra', 'rop_geometry', 'rop_alembic', 'usdrender', 'fetch', 'merge', 'rop_fbx', 'rop_gltf').
        name: Node name.
        camera: Camera node path.
        output_path: Output file path.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"renderer": renderer}
    if name is not None:
        params["name"] = name
    if camera is not None:
        params["camera"] = camera
    if output_path is not None:
        params["output_path"] = output_path
    return await bridge.execute("rendering.create_render_node", params)


@mcp.tool()
async def start_render(
    ctx: Context,
    node_path: str,
    frame_range: list[float] | None = None,
    timeout: float | None = None,
) -> dict:
    """Render a ROP node.

    This blocks on Houdini's main thread until the render finishes, so any
    non-trivial render (or a frame range) needs an explicit `timeout` — the
    default (120s) will abandon the wait while the render keeps going, and the
    result (output path) is then lost to the client. Size `timeout` to the
    expected render time, and poll get_render_progress for status.

    Args:
        node_path: ROP node path.
        frame_range: [start, end] or [start, end, increment].
        timeout: Operation budget in seconds. Omit for the default (120s);
            raise it for real renders.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"node_path": node_path}
    if frame_range is not None:
        params["frame_range"] = frame_range
    return await bridge.execute("rendering.start_render", params, timeout=timeout)


@mcp.tool()
async def render_node_network(
    ctx: Context,
    node_path: str,
    output_path: str,
) -> dict:
    """Capture a screenshot of a node's network editor view.

    Args:
        node_path: Node path to focus on.
        output_path: Image file path.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "rendering.render_node_network",
        {"node_path": node_path, "output_path": output_path},
    )


@mcp.tool()
async def get_render_progress(ctx: Context, node_path: str) -> dict:
    """Get render progress and status of a ROP node.

    Args:
        node_path: ROP node path.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "rendering.get_render_progress", {"node_path": node_path}
    )


@mcp.tool()
async def render_rop(
    ctx: Context,
    node_path: str,
    frame_range: list[float] | None = None,
    use_render_button: bool = False,
    button_parm: str | None = None,
    verbose: bool = False,
    timeout: float | None = 600.0,
) -> dict:
    """Render a ROP synchronously, optionally via its render BUTTON.

    Use this for any non-trivial render and ALWAYS for UI-dependent HDA bakes.
    Some HDAs — notably SideFX Labs "Vertex Animation Textures" — only run
    their real multi-pass bake when their render BUTTON is pressed;
    `node.render()` silently produces empty output. Set `use_render_button=True`
    for those: the button parm is `button_parm` if given, else the first
    existing of `renderall` / `execute` / `render` / `rendersingle`.

    This blocks Houdini's main thread until the render finishes, so `timeout`
    defaults to 600s (vs the dispatcher's usual 120s) — raise it further for
    heavy renders. Prefer this over the async `start_render_job` for HDA render
    buttons, which the headless subprocess path cannot run reliably.

    Args:
        node_path: ROP node path.
        frame_range: [start, end] or [start, end, increment]. Ignored when
            `use_render_button` is set.
        use_render_button: Press the HDA render button instead of node.render().
        button_parm: Explicit render-button parm name to press.
        verbose: Pass verbose=True to node.render().
        timeout: Operation budget in seconds (default 600s). Raise for heavy
            renders.

    Returns:
        Dict with node_path, rendered, used_button, button_parm, frame_range,
        errors, warnings, and outputs (each {parm, path, exists, size, mtime}).
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {
        "node_path": node_path,
        "use_render_button": use_render_button,
        "verbose": verbose,
    }
    if frame_range is not None:
        params["frame_range"] = frame_range
    if button_parm is not None:
        params["button_parm"] = button_parm
    return await bridge.execute("rendering.render_rop", params, timeout=timeout)


@mcp.tool()
async def list_rop_outputs(ctx: Context, node_path: str) -> dict:
    """List every resolved output file path declared on a ROP node.

    Resolves all known output-path parms plus any `path_*` / `output*` /
    `file*` string parm (covering SideFX Labs HDAs such as Vertex Animation
    Textures), expands variables, and stats each file. Use this to confirm a
    render — especially an HDA button bake — actually wrote its files.

    Args:
        node_path: ROP node path.

    Returns:
        Dict with node_path and outputs (de-duped by resolved path; each
        {parm, path, exists, size, mtime}).
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "rendering.list_rop_outputs", {"node_path": node_path}
    )


@mcp.tool()
async def start_render_job(
    ctx: Context,
    node_path: str,
    frame_range: list[float] | None = None,
    use_render_button: bool = False,
    button_parm: str | None = None,
) -> dict:
    """Launch a detached hython render job and return immediately (non-blocking).

    Saves a sidecar copy of the current hip (the live session keeps its own
    file association) and spawns `hython` to render OUTSIDE the interactive
    session, so nothing blocks Houdini's main thread or the dispatcher budget.
    Best for standard ROPs (Mantra/Karma/Alembic/geometry). Poll progress with
    get_render_job; list with list_render_jobs; stop with cancel_render_job.

    CAVEAT: hython has no UI, so HDA render BUTTONS whose callback needs
    `hou.ui` (e.g. Labs "Vertex Animation Textures" "Render All") may not work
    in this async mode — use the synchronous render_rop with a generous
    timeout for those instead.

    Args:
        node_path: ROP node path.
        frame_range: [start, end] or [start, end, increment].
        use_render_button: Press the ROP's render button (see caveat).
        button_parm: Explicit render-button parm name.

    Returns:
        Dict with job_id, pid, and status_file (returned without waiting).
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {
        "node_path": node_path,
        "use_render_button": use_render_button,
    }
    if frame_range is not None:
        params["frame_range"] = frame_range
    if button_parm is not None:
        params["button_parm"] = button_parm
    return await bridge.execute("rendering.start_render_job", params)


@mcp.tool()
async def get_render_job(ctx: Context, job_id: str) -> dict:
    """Report the state of an async render job started by start_render_job.

    Args:
        job_id: The id returned by start_render_job.

    Returns:
        Dict with job_id, state ("running"/"done"/"failed"/"unknown"),
        returncode, elapsed, log_tail, and outputs.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("rendering.get_render_job", {"job_id": job_id})


@mcp.tool()
async def list_render_jobs(ctx: Context) -> dict:
    """List all async render jobs tracked this session with their state."""
    bridge = _get_bridge(ctx)
    return await bridge.execute("rendering.list_render_jobs", {})


@mcp.tool()
async def cancel_render_job(ctx: Context, job_id: str) -> dict:
    """Terminate a running async render job's process.

    Args:
        job_id: The id returned by start_render_job.

    Returns:
        Dict with job_id, cancelled (bool), and a message.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "rendering.cancel_render_job", {"job_id": job_id}
    )
