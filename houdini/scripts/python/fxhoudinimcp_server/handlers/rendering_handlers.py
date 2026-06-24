"""Rendering handlers for FXHoudini-MCP.

Provides tools for viewport capture, render node management, and rendering
operations including Karma, OpenGL, and other Houdini renderers.
"""

from __future__ import annotations

# Built-in
import json
import logging
import os
import subprocess
import sys
import time
import uuid

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_handler

logger = logging.getLogger(__name__)

# Module-global registry of async render jobs launched by start_render_job.
# Maps job_id -> {"pid", "hip", "rop", "status_file", "started_ts", "process"}.
# The "process" entry holds the live subprocess.Popen handle when available.
_RENDER_JOBS: dict[str, dict] = {}

# Render button parm names tried, in order, when use_render_button is set with
# no explicit button_parm. Shared by render_rop and the async worker.
_RENDER_BUTTON_CANDIDATES = ("renderall", "execute", "render", "rendersingle")

# Output-path parm names checked explicitly, plus any string parm whose name
# starts with one of _OUTPUT_PARM_PREFIXES (covers Labs VAT path_pos/path_geo).
_OUTPUT_PARM_NAMES = (
    "sopoutput",
    "copoutput",
    "lopoutput",
    "dopoutput",
    "picture",
    "vm_picture",
    "output",
    "filename",
    "file",
)
_OUTPUT_PARM_PREFIXES = ("path_", "output", "file")


###### rendering.render_viewport

def _find_flipbook_output(output_path: str, frame: float) -> str:
    """Find the actual output file after flipbook, handling frame number insertion.

    Houdini's flipbook may insert a frame number into the filename
    (e.g. "output.0001.png" instead of "output.png") even for single-frame
    captures. This helper locates the actual file and optionally renames it.
    """
    # A Houdini frame token (for example ``$F4``) expands *inside* the file
    # name. Check the expanded path before trying the fallback patterns; using
    # the raw value would look for ``image.$F4.0001.png`` even though flipbook
    # correctly wrote ``image.0001.png``.
    expanded_path = hou.expandString(output_path)
    if os.path.isfile(expanded_path):
        return expanded_path
    if os.path.isfile(output_path):
        return output_path

    # Try common frame-number patterns
    base, ext = os.path.splitext(expanded_path)
    frame_int = int(frame)
    candidates = [
        f"{base}.{frame_int:04d}{ext}",
        f"{base}.{frame_int:03d}{ext}",
        f"{base}.{frame_int}{ext}",
        f"{base}_{frame_int:04d}{ext}",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            # Rename to the originally requested path
            try:
                os.rename(candidate, output_path)
                return output_path
            except OSError:
                return candidate

    return output_path


def render_viewport(
    output_path: str,
    resolution: list = None,
    camera: str = None,
) -> dict:
    """Capture the current viewport to an image file.

    Args:
        output_path: Destination image path (e.g. .png, .jpg, .exr).
        resolution: Optional [width, height] override.
        camera: Optional camera node path to look through before capture.
    """
    # Ensure the output directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Get the current scene viewer
    scene_viewer = None
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.SceneViewer:
            scene_viewer = pane_tab
            break

    if scene_viewer is None:
        raise RuntimeError(
            "No Scene Viewer pane found. A viewport must be open to capture."
        )

    viewport = scene_viewer.curViewport()

    # Optionally set the camera
    if camera is not None:
        cam_node = hou.node(camera)
        if cam_node is None:
            raise ValueError(f"Camera node not found: {camera}")
        viewport.setCamera(cam_node)

    cur_frame = hou.frame()

    # Build the flipbook settings for image capture
    settings = scene_viewer.flipbookSettings().stash()
    settings.frameRange((cur_frame, cur_frame))
    settings.output(output_path)

    if resolution is not None:
        if len(resolution) != 2:
            raise ValueError("resolution must be a list of [width, height]")
        settings.useResolution(True)
        settings.resolution(tuple(resolution))

    # Use the flipbook approach for a single-frame capture
    scene_viewer.flipbook(viewport, settings)

    # Handle frame number that flipbook may insert into the filename
    actual_path = _find_flipbook_output(output_path, cur_frame)

    # Downscale + JPEG-compress before base64 to avoid token bloat.
    if not os.path.isfile(actual_path):
        raise hou.OperationFailed(
            f"Viewport render did not produce an image at '{actual_path}'."
        )
    from fxhoudinimcp_server.handlers.viewport_handlers import _downscale_and_encode
    image_base64, mime_type = _downscale_and_encode(actual_path)
    if not image_base64:
        raise hou.OperationFailed(
            f"Viewport render exists but could not be encoded for MCP: '{actual_path}'."
        )

    return {
        "success": True,
        "output_path": actual_path,
        "file_exists": True,
        "resolution": resolution,
        "camera": camera,
        "frame": cur_frame,
        "image_base64": image_base64,
        "mime_type": mime_type,
    }


###### rendering.render_quad_view

def render_quad_view(
    output_path: str,
    resolution: list = None,
) -> dict:
    """Capture all four viewport panes (quad view) to an image file.

    Args:
        output_path: Destination image path.
        resolution: Optional [width, height] override.
    """
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    scene_viewer = None
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.SceneViewer:
            scene_viewer = pane_tab
            break

    if scene_viewer is None:
        raise RuntimeError("No Scene Viewer pane found.")

    viewports = scene_viewer.viewports()
    if not viewports:
        raise RuntimeError("No viewports available in the Scene Viewer.")

    saved_files = []
    base, ext = os.path.splitext(output_path)

    for vp in viewports:
        vp_name = vp.name()
        vp_output = f"{base}_{vp_name}{ext}"

        settings = scene_viewer.flipbookSettings().stash()
        settings.frameRange((hou.frame(), hou.frame()))
        settings.output(vp_output)

        if resolution is not None:
            if len(resolution) != 2:
                raise ValueError("resolution must be a list of [width, height]")
            settings.resolution(tuple(resolution))

        scene_viewer.flipbook(vp, settings)
        saved_files.append({"viewport": vp_name, "output_path": vp_output})

    return {
        "success": True,
        "viewports": saved_files,
        "frame": hou.frame(),
    }


###### rendering.list_render_nodes

def list_render_nodes() -> dict:
    """List all ROP (render) nodes in /out and embedded in other networks.

    Searches for all nodes whose type category is 'Driver'.
    """
    render_nodes = []

    def _collect_rops(parent):
        """Recursively collect all Driver-category nodes."""
        for child in parent.children():
            try:
                cat = child.type().category().name()
            except (hou.ObjectWasDeleted, AttributeError) as e:
                logger.debug("Could not read category for node: %s", e)
                continue
            if cat == "Driver":
                info = {
                    "name": child.name(),
                    "path": child.path(),
                    "type": child.type().name(),
                    "description": child.type().description(),
                }
                # Safely retrieve common render parameters
                try:
                    cam_parm = child.parm("camera")
                    info["camera"] = cam_parm.eval() if cam_parm else None
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read camera parm for '%s': %s", child.path(), e)
                    info["camera"] = None
                try:
                    out_parm = (
                        child.parm("vm_picture")
                        or child.parm("copoutput")
                        or child.parm("sopoutput")
                        or child.parm("picture")
                    )
                    info["output"] = out_parm.eval() if out_parm else None
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read output parm for '%s': %s", child.path(), e)
                    info["output"] = None
                render_nodes.append(info)
            # Recurse into children regardless of category
            try:
                if child.children():
                    _collect_rops(child)
            except (hou.OperationFailed, hou.ObjectWasDeleted) as e:
                logger.debug("Could not recurse into children of '%s': %s", child.path(), e)

    _collect_rops(hou.node("/"))

    return {
        "render_nodes": render_nodes,
        "count": len(render_nodes),
    }


###### rendering.get_render_settings

def get_render_settings(node_path: str) -> dict:
    """Get key render settings from a ROP node.

    Args:
        node_path: Path to the ROP/Driver node.
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    if node.type().category().name() != "Driver":
        raise ValueError(
            f"Node {node_path} is not a ROP/Driver node "
            f"(category: {node.type().category().name()})."
        )

    settings = {
        "node_path": node.path(),
        "node_type": node.type().name(),
        "description": node.type().description(),
    }

    # Common render parameters to extract
    parm_names = [
        "camera", "vm_picture", "picture", "copoutput", "sopoutput",
        "res_overridex", "res_overridey", "resx", "resy",
        "resoverride", "res",
        "f1", "f2", "f3",  # frame range start, end, increment
        "trange",  # time range mode
        "override_camerares",
        "renderer",
        "vm_renderengine",
    ]

    for parm_name in parm_names:
        try:
            parm = node.parm(parm_name)
            if parm is not None:
                val = parm.eval()
                # Convert hou types to plain Python
                if hasattr(val, "path"):
                    val = val.path()
                settings[parm_name] = val
        except (hou.OperationFailed, AttributeError) as e:
            logger.debug("Could not read render parm '%s': %s", parm_name, e)

    # Check for parm tuples (e.g. resolution)
    for tuple_name in ["res", "t"]:
        try:
            pt = node.parmTuple(tuple_name)
            if pt is not None:
                settings[tuple_name] = [p.eval() for p in pt]
        except (hou.OperationFailed, AttributeError) as e:
            logger.debug("Could not read render parm tuple '%s': %s", tuple_name, e)

    return settings


###### rendering.set_render_settings

def set_render_settings(node_path: str, settings: dict) -> dict:
    """Set render parameters on a ROP node.

    Args:
        node_path: Path to the ROP/Driver node.
        settings: Dict of parameter_name -> value pairs to set.
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    if node.type().category().name() != "Driver":
        raise ValueError(
            f"Node {node_path} is not a ROP/Driver node "
            f"(category: {node.type().category().name()})."
        )

    applied = {}
    errors = {}

    for parm_name, value in settings.items():
        try:
            parm = node.parm(parm_name)
            if parm is None:
                # Try as a parm tuple
                pt = node.parmTuple(parm_name)
                if pt is not None:
                    pt.set(value)
                    applied[parm_name] = value
                else:
                    errors[parm_name] = f"Parameter not found: {parm_name}"
            else:
                parm.set(value)
                applied[parm_name] = value
        except Exception as e:
            errors[parm_name] = str(e)

    return {
        "success": len(errors) == 0,
        "node_path": node.path(),
        "applied": applied,
        "errors": errors if errors else None,
    }


###### rendering.create_render_node

def create_render_node(
    renderer: str,
    name: str = None,
    camera: str = None,
    output_path: str = None,
) -> dict:
    """Create a new render (ROP) node in /out.

    Args:
        renderer: Renderer type. Supported values include:
            'karma' (USD Karma), 'opengl' (OpenGL), 'ifd' (Mantra),
            'rop_geometry' (Geometry ROP), 'fetch', 'merge', etc.
        name: Optional node name. Auto-generated if not provided.
        camera: Optional camera path to assign.
        output_path: Optional output image/file path.
    """
    out_context = hou.node("/out")
    if out_context is None:
        raise RuntimeError("/out context not found.")

    # Map friendly renderer names to actual node types
    renderer_map = {
        "karma": "karma",
        "opengl": "opengl",
        "mantra": "ifd",
        "ifd": "ifd",
        "geometry": "rop_geometry",
        "rop_geometry": "rop_geometry",
        "alembic": "rop_alembic",
        "rop_alembic": "rop_alembic",
        "fetch": "fetch",
        "merge": "merge",
        "usdrender": "usdrender",
        "usd_rop": "usd_rop",
        "filmboxfbx": "filmboxfbx",
        "comp": "comp",
        "wedge": "wedge",
        "baketexture": "baketexture",
    }

    node_type = renderer_map.get(renderer.lower(), renderer)

    try:
        node = out_context.createNode(node_type, name)
    except hou.OperationFailed as e:
        raise ValueError(
            f"Failed to create render node of type '{node_type}': {e}"
        )

    # Set camera if provided
    if camera is not None:
        cam_parm = node.parm("camera")
        if cam_parm is not None:
            cam_parm.set(camera)

    # Set output path if provided
    if output_path is not None:
        # Try common output parameter names
        for parm_name in ("vm_picture", "picture", "copoutput", "sopoutput"):
            parm = node.parm(parm_name)
            if parm is not None:
                parm.set(output_path)
                break

    node.moveToGoodPosition()

    return {
        "success": True,
        "node_path": node.path(),
        "node_type": node.type().name(),
        "renderer": renderer,
    }


###### rendering.start_render

def start_render(
    node_path: str,
    frame_range: list = None,
) -> dict:
    """Begin rendering a ROP node.

    Args:
        node_path: Path to the ROP/Driver node.
        frame_range: Optional [start, end] frame range. If not provided,
            renders with the node's own frame range settings.
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    if node.type().category().name() != "Driver":
        raise ValueError(
            f"Node {node_path} is not a ROP/Driver node "
            f"(category: {node.type().category().name()})."
        )

    try:
        if frame_range is not None:
            if len(frame_range) < 2:
                raise ValueError("frame_range must have at least [start, end].")
            start = float(frame_range[0])
            end = float(frame_range[1])
            inc = float(frame_range[2]) if len(frame_range) > 2 else 1.0
            # RopNode.render takes the increment as the third element of
            # frame_range; there is no frame_increment keyword.
            node.render(
                frame_range=(start, end, inc),
                output_progress=True,
            )
        else:
            node.render(output_progress=True)
    except hou.OperationFailed as e:
        return {
            "success": False,
            "node_path": node_path,
            "error": str(e),
        }

    return {
        "success": True,
        "node_path": node_path,
        "frame_range": frame_range,
        "message": "Render completed.",
    }


###### rendering.render_node_network

def render_node_network(
    node_path: str,
    output_path: str,
) -> dict:
    """Take a screenshot of the network editor showing a specific node's network.

    Args:
        node_path: Path to the node whose network to capture.
        output_path: Destination image path.
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Find a network editor pane tab
    network_editor = None
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.NetworkEditor:
            network_editor = pane_tab
            break

    if network_editor is None:
        raise RuntimeError("No Network Editor pane found.")

    # Navigate to the node's parent network so the node is visible
    parent = node.parent()
    if parent is not None:
        network_editor.cd(parent.path())

    # Frame the node in the editor
    network_editor.setCurrentNode(node)
    network_editor.homeToSelection()

    # Capture the network editor as an image via Qt widget grab
    from fxhoudinimcp_server.handlers.viewport_handlers import (
        _capture_pane_tab_qt, _downscale_and_encode,
    )
    _capture_pane_tab_qt(network_editor, output_path)

    if not os.path.isfile(output_path):
        raise hou.OperationFailed(
            f"Network render did not produce an image at '{output_path}'."
        )
    image_base64, mime_type = _downscale_and_encode(output_path)
    if not image_base64:
        raise hou.OperationFailed(
            f"Network render exists but could not be encoded for MCP: '{output_path}'."
        )

    return {
        "success": True,
        "node_path": node_path,
        "output_path": output_path,
        "image_base64": image_base64,
        "mime_type": mime_type,
    }


###### rendering.get_render_progress

def get_render_progress(node_path: str) -> dict:
    """Check the render status / progress of a ROP node.

    Args:
        node_path: Path to the ROP/Driver node.
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    if node.type().category().name() != "Driver":
        raise ValueError(
            f"Node {node_path} is not a ROP/Driver node "
            f"(category: {node.type().category().name()})."
        )

    is_cooking = node.isCooking() if hasattr(node, "isCooking") else False

    # Check cook count as a proxy for activity
    try:
        cook_count = node.cookCount()
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read cook count for '%s': %s", node_path, e)
        cook_count = None

    # Check for errors and warnings
    try:
        errors = list(node.errors()) if node.errors() else []
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read errors for '%s': %s", node_path, e)
        errors = []

    try:
        warnings = list(node.warnings()) if node.warnings() else []
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read warnings for '%s': %s", node_path, e)
        warnings = []

    # Retrieve the output file to check if it exists on disk
    output_file = None
    for parm_name in ("vm_picture", "picture", "copoutput", "sopoutput"):
        try:
            parm = node.parm(parm_name)
            if parm is not None:
                output_file = parm.eval()
                break
        except (hou.OperationFailed, AttributeError) as e:
            logger.debug("Could not read output parm '%s': %s", parm_name, e)

    output_exists = False
    if output_file:
        try:
            output_exists = os.path.isfile(output_file)
        except OSError as e:
            logger.debug("Could not check output file existence: %s", e)

    return {
        "node_path": node.path(),
        "is_cooking": is_cooking,
        "cook_count": cook_count,
        "errors": errors,
        "warnings": warnings,
        "output_file": output_file,
        "output_exists": output_exists,
    }


###### rendering.list_rop_outputs

def _resolve_rop_outputs(node) -> list:
    """Resolve every output-path parm on a ROP to a stat-ed file entry.

    Checks the known output-parm names first, then any string parm whose name
    starts with ``path_`` / ``output`` / ``file`` so SideFX Labs HDAs that
    write to ``path_pos`` / ``path_geo`` / ``path_n`` (e.g. Vertex Animation
    Textures) are captured too. Each parm is evaluated then run through
    ``hou.expandString`` before stat-ing. Results are de-duped by resolved
    path.

    Args:
        node: The ROP/Driver ``hou.Node`` to inspect.

    Returns:
        A list of ``{"parm", "path", "exists", "size", "mtime"}`` dicts.
    """
    outputs: list = []
    seen_paths: set = set()

    def _consider(parm) -> None:
        if parm is None:
            return
        try:
            raw = parm.eval()
        except (hou.OperationFailed, AttributeError, TypeError) as exc:
            logger.debug("Could not eval parm '%s': %s", parm.name(), exc)
            return
        if not raw or not isinstance(raw, str):
            return
        try:
            resolved = hou.expandString(raw)
        except hou.OperationFailed as exc:
            logger.debug("Could not expand '%s': %s", raw, exc)
            resolved = raw
        if not resolved or resolved in seen_paths:
            return
        seen_paths.add(resolved)

        exists = False
        size = None
        mtime = None
        try:
            if os.path.isfile(resolved):
                stat = os.stat(resolved)
                exists = True
                size = stat.st_size
                mtime = stat.st_mtime
        except OSError as exc:
            logger.debug("Could not stat '%s': %s", resolved, exc)

        outputs.append(
            {
                "parm": parm.name(),
                "path": resolved,
                "exists": exists,
                "size": size,
                "mtime": mtime,
            }
        )

    for parm_name in _OUTPUT_PARM_NAMES:
        _consider(node.parm(parm_name))

    try:
        all_parms = node.parms()
    except (hou.OperationFailed, AttributeError) as exc:
        logger.debug("Could not list parms for '%s': %s", node.path(), exc)
        all_parms = ()
    for parm in all_parms:
        try:
            name = parm.name()
        except (hou.OperationFailed, AttributeError):
            continue
        if name.startswith(_OUTPUT_PARM_PREFIXES):
            _consider(parm)

    return outputs


def list_rop_outputs(node_path: str) -> dict:
    """List every resolved output file path declared on a ROP node.

    Resolves all known output-path parms plus any ``path_*`` / ``output*`` /
    ``file*`` string parm (covering SideFX Labs HDAs such as Vertex Animation
    Textures), expands variables, and stats each file. Useful for confirming
    that a render (especially an HDA button bake) actually wrote its files.

    Args:
        node_path: Path to the ROP/Driver node.

    Returns:
        Dict with ``node_path`` and ``outputs`` (de-duped by resolved path).
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    return {
        "node_path": node.path(),
        "outputs": _resolve_rop_outputs(node),
    }


###### rendering.render_rop

def render_rop(
    node_path: str,
    frame_range: list = None,
    use_render_button: bool = False,
    button_parm: str = None,
    verbose: bool = False,
) -> dict:
    """Render a ROP node synchronously, optionally via its render BUTTON.

    For most ROPs (Mantra/Karma/Alembic/geometry) ``node.render()`` is correct.
    But some HDAs — notably SideFX Labs "Vertex Animation Textures" — only run
    their real multi-pass bake when their render BUTTON is pressed;
    ``node.render()`` silently produces empty output. Set
    ``use_render_button=True`` for those: the button parm is ``button_parm`` if
    given, else the first existing of ``renderall`` / ``execute`` / ``render``
    / ``rendersingle``.

    This blocks Houdini's main thread until the render finishes, so the caller
    must pass a generous ``timeout`` through the MCP layer (default 600s) for
    real renders. This is the RECOMMENDED path for UI-dependent HDA bakes,
    which the async/subprocess path cannot run reliably.

    Args:
        node_path: Path to the ROP/Driver node.
        frame_range: Optional ``[start, end]`` or ``[start, end, increment]``.
            Ignored when ``use_render_button`` is set.
        use_render_button: Press the HDA render button instead of calling
            ``node.render()``.
        button_parm: Explicit render-button parm name to press.
        verbose: Pass ``verbose=True`` to ``node.render()``.

    Returns:
        Dict with ``node_path``, ``rendered``, ``used_button``,
        ``button_parm``, ``frame_range``, ``errors``, ``warnings``, and
        ``outputs`` (resolved via :func:`list_rop_outputs`).
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    used_button = False
    button_name = None

    if use_render_button:
        if button_parm:
            btn = node.parm(button_parm)
            if btn is None:
                raise ValueError(
                    f"Render button parm '{button_parm}' not found on "
                    f"{node_path}."
                )
        else:
            btn = None
            for candidate in _RENDER_BUTTON_CANDIDATES:
                btn = node.parm(candidate)
                if btn is not None:
                    break
            if btn is None:
                raise ValueError(
                    f"No render button parm found on {node_path}. Tried: "
                    f"{list(_RENDER_BUTTON_CANDIDATES)}. Pass an explicit "
                    "button_parm."
                )
        used_button = True
        button_name = btn.name()
        btn.pressButton()
    elif frame_range is not None:
        if len(frame_range) < 2:
            raise ValueError("frame_range must have at least [start, end].")
        start = float(frame_range[0])
        end = float(frame_range[1])
        inc = float(frame_range[2]) if len(frame_range) > 2 else 1.0
        node.render(frame_range=(start, end, inc), verbose=verbose)
    else:
        node.render(verbose=verbose)

    try:
        errors = list(node.errors()) if node.errors() else []
    except (hou.OperationFailed, AttributeError) as exc:
        logger.debug("Could not read errors for '%s': %s", node_path, exc)
        errors = []
    try:
        warnings = list(node.warnings()) if node.warnings() else []
    except (hou.OperationFailed, AttributeError) as exc:
        logger.debug("Could not read warnings for '%s': %s", node_path, exc)
        warnings = []

    return {
        "node_path": node.path(),
        "rendered": True,
        "used_button": used_button,
        "button_parm": button_name,
        "frame_range": frame_range,
        "errors": errors,
        "warnings": warnings,
        "outputs": _resolve_rop_outputs(node),
    }


###### rendering.start_render_job

def _worker_script_path() -> str:
    """Return the absolute path to the standalone _render_worker.py script."""
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), os.pardir, "_render_worker.py")
    )


def _job_dir() -> str:
    """Return (creating if needed) the sidecar directory for async render jobs.

    Placed next to the current hip file as ``.fxmcp_renderjobs`` so temp hips
    and status files live with the project; falls back to a temp dir for an
    unsaved scene.
    """
    hip_path = hou.hipFile.path()
    base = os.path.dirname(hip_path) if hip_path else ""
    if not base or not os.path.isdir(base):
        import tempfile

        base = tempfile.gettempdir()
    job_dir = os.path.join(base, ".fxmcp_renderjobs")
    os.makedirs(job_dir, exist_ok=True)
    return job_dir


def start_render_job(
    node_path: str,
    frame_range: list = None,
    use_render_button: bool = False,
    button_parm: str = None,
) -> dict:
    """Launch a detached hython process that renders a ROP, returning at once.

    The current hip is saved to a temporary sidecar (its file association is
    immediately restored so the interactive session keeps its original name),
    then ``hython _render_worker.py`` is spawned to render OUTSIDE the live
    session. This never blocks Houdini's main thread or the dispatcher budget,
    and is the best path for standard ROPs (Mantra/Karma/Alembic/geometry).

    CAVEAT: hython has no UI, so HDA render BUTTONS whose callback needs
    ``hou.ui`` (e.g. Labs "Vertex Animation Textures" "Render All") may not work
    here — use the synchronous :func:`render_rop` with a generous ``timeout``
    for those instead.

    Args:
        node_path: Path to the ROP/Driver node.
        frame_range: Optional ``[start, end]`` or ``[start, end, increment]``.
        use_render_button: Press the ROP's render button (see caveat).
        button_parm: Explicit render-button parm name.

    Returns:
        Dict with ``job_id``, ``pid``, and ``status_file``.
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    hfs = hou.expandString("$HFS")
    hython = os.path.join(hfs, "bin", "hython.exe")
    if not os.path.isfile(hython):
        # Non-Windows fallback; primary target is Windows hython.exe.
        alt = os.path.join(hfs, "bin", "hython")
        hython = alt if os.path.isfile(alt) else hython

    worker = _worker_script_path()
    if not os.path.isfile(worker):
        raise RuntimeError(f"Render worker script not found: {worker}")

    job_id = uuid.uuid4().hex
    job_dir = _job_dir()
    temp_hip = os.path.join(job_dir, f"{job_id}.hip")
    status_file = os.path.join(job_dir, f"{job_id}.json")

    # Save a sidecar copy for the worker, then restore the session's own file
    # association so the user's interactive session is unchanged.
    original = hou.hipFile.path()
    hou.hipFile.save(file_name=temp_hip, save_to_recent_files=False)
    if original:
        hou.hipFile.setName(original)

    cmd = [hython, worker, temp_hip, node.path(), status_file]
    if not use_render_button and frame_range is not None and len(frame_range) >= 2:
        start = float(frame_range[0])
        end = float(frame_range[1])
        inc = float(frame_range[2]) if len(frame_range) > 2 else 1.0
        cmd += [str(start), str(end), str(inc)]
    if use_render_button:
        cmd.append("--button")
        if button_parm:
            cmd.append(button_parm)

    # Detach so the render outlives this dispatch call. On Windows, a new
    # process group prevents the child from dying with the parent console.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    log_file = os.path.join(job_dir, f"{job_id}.log")
    # The handle must outlive this function so the detached child can keep
    # writing to it; a context manager would close it immediately. The OS
    # reclaims the descriptor when the worker process exits.
    log_handle = open(log_file, "w", encoding="utf-8")  # noqa: SIM115
    process = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        close_fds=True,
    )

    started_ts = time.time()
    _RENDER_JOBS[job_id] = {
        "pid": process.pid,
        "hip": temp_hip,
        "rop": node.path(),
        "status_file": status_file,
        "log_file": log_file,
        "started_ts": started_ts,
        "process": process,
        "frame_range": frame_range,
        "use_render_button": use_render_button,
        "button_parm": button_parm,
    }

    return {
        "job_id": job_id,
        "pid": process.pid,
        "status_file": status_file,
    }


###### rendering.get_render_job

def _process_alive(pid: int, process) -> bool | None:
    """Best-effort liveness check for a render job's process.

    Prefers the live ``Popen`` handle, then ``psutil`` if installed, else
    returns ``None`` (unknown) so the status file remains the source of truth.
    """
    if process is not None:
        return process.poll() is None
    try:
        import psutil  # type: ignore

        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001 - psutil optional / probe is best-effort
        return None


def _read_status_file(status_file: str) -> dict:
    """Read and JSON-parse a worker status file, tolerating partial writes."""
    if not status_file or not os.path.isfile(status_file):
        return {}
    try:
        with open(status_file, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        logger.debug("Could not read status file '%s': %s", status_file, exc)
        return {}


def _read_log_tail(log_file: str, max_chars: int = 4000) -> str:
    """Return the tail of a job's log file, capped at ``max_chars``."""
    if not log_file or not os.path.isfile(log_file):
        return ""
    try:
        with open(log_file, encoding="utf-8", errors="replace") as handle:
            data = handle.read()
    except OSError as exc:
        logger.debug("Could not read log file '%s': %s", log_file, exc)
        return ""
    return data[-max_chars:]


def get_render_job(job_id: str) -> dict:
    """Report the state of an async render job started by start_render_job.

    Combines the worker's status JSON with a liveness check on the process. If
    the worker reports ``done`` / ``failed`` that is authoritative; otherwise
    the process liveness disambiguates ``running`` from a crashed worker that
    never wrote a terminal status.

    Args:
        job_id: The id returned by :func:`start_render_job`.

    Returns:
        Dict with ``job_id``, ``state`` (``running`` / ``done`` / ``failed`` /
        ``unknown``), ``returncode``, ``elapsed``, ``log_tail`` and
        ``outputs``.
    """
    job = _RENDER_JOBS.get(job_id)
    if job is None:
        raise ValueError(f"Unknown render job: {job_id}")

    status = _read_status_file(job["status_file"])
    process = job.get("process")
    alive = _process_alive(job["pid"], process)

    returncode = status.get("returncode")
    if returncode is None and process is not None:
        returncode = process.poll()

    worker_state = status.get("state")
    if worker_state in ("done", "failed"):
        state = worker_state
    elif worker_state == "running":
        # Worker said running; if the process is gone without a terminal
        # status it crashed.
        state = "running" if alive in (True, None) else "failed"
    elif alive is True:
        state = "running"
    elif alive is False:
        # Process exited before writing any status — failed unless rc says 0.
        state = "done" if returncode == 0 else "failed"
    else:
        state = "unknown"

    started_ts = job.get("started_ts")
    elapsed = status.get("elapsed")
    if elapsed is None and started_ts is not None:
        elapsed = time.time() - started_ts

    # Prefer freshly resolved outputs from the live node when this is the
    # interactive session's own ROP; fall back to the worker-reported outputs.
    outputs = status.get("outputs")
    if not outputs:
        node = hou.node(job["rop"])
        outputs = _resolve_rop_outputs(node) if node is not None else []

    return {
        "job_id": job_id,
        "state": state,
        "returncode": returncode,
        "elapsed": elapsed,
        "log_tail": _read_log_tail(job.get("log_file", "")),
        "outputs": outputs,
        "error": status.get("error"),
        "status_file": job["status_file"],
    }


###### rendering.list_render_jobs

def list_render_jobs() -> dict:
    """List all async render jobs tracked this session with their state.

    Returns:
        Dict with ``jobs`` (a list of per-job summaries) and ``count``.
    """
    jobs = []
    for job_id, job in _RENDER_JOBS.items():
        status = _read_status_file(job["status_file"])
        process = job.get("process")
        alive = _process_alive(job["pid"], process)
        worker_state = status.get("state")
        if worker_state in ("done", "failed"):
            state = worker_state
        elif alive is True:
            state = "running"
        elif alive is False:
            state = "done" if status.get("returncode") == 0 else "failed"
        else:
            state = worker_state or "unknown"
        jobs.append(
            {
                "job_id": job_id,
                "state": state,
                "pid": job["pid"],
                "rop": job["rop"],
                "status_file": job["status_file"],
                "started_ts": job.get("started_ts"),
            }
        )
    return {"jobs": jobs, "count": len(jobs)}


###### rendering.cancel_render_job

def cancel_render_job(job_id: str) -> dict:
    """Terminate a running async render job's process.

    Args:
        job_id: The id returned by :func:`start_render_job`.

    Returns:
        Dict with ``job_id``, ``cancelled`` (bool), and a ``message``.
    """
    job = _RENDER_JOBS.get(job_id)
    if job is None:
        raise ValueError(f"Unknown render job: {job_id}")

    process = job.get("process")
    if process is None:
        return {
            "job_id": job_id,
            "cancelled": False,
            "message": "No live process handle; cannot terminate.",
        }

    if process.poll() is not None:
        return {
            "job_id": job_id,
            "cancelled": False,
            "message": f"Process already exited (returncode={process.returncode}).",
        }

    try:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    except OSError as exc:
        return {
            "job_id": job_id,
            "cancelled": False,
            "message": f"Could not terminate process: {exc}",
        }

    return {
        "job_id": job_id,
        "cancelled": True,
        "message": "Render job terminated.",
    }


###### Registration

register_handler("rendering.render_viewport", render_viewport)
register_handler("rendering.render_quad_view", render_quad_view)
register_handler("rendering.list_render_nodes", list_render_nodes)
register_handler("rendering.get_render_settings", get_render_settings)
register_handler("rendering.set_render_settings", set_render_settings)
register_handler("rendering.create_render_node", create_render_node)
register_handler("rendering.start_render", start_render)
register_handler("rendering.render_node_network", render_node_network)
register_handler("rendering.get_render_progress", get_render_progress)
register_handler("rendering.render_rop", render_rop)
register_handler("rendering.list_rop_outputs", list_rop_outputs)
register_handler("rendering.start_render_job", start_render_job)
register_handler("rendering.get_render_job", get_render_job)
register_handler("rendering.list_render_jobs", list_render_jobs)
register_handler("rendering.cancel_render_job", cancel_render_job)
