"""MCP tool wrappers for image inspection.

Each tool delegates to the corresponding handler running inside Houdini via
the HTTP bridge. The handlers use OpenImageIO (which ships inside Houdini's
Python) to inspect rendered images, VAT textures, and EXRs without
hand-written execute_python. These tools return statistics dicts; they do not
attach inline image content.
"""

from __future__ import annotations

# Third-party
from mcp.server.fastmcp import Context

# Internal
from fxhoudinimcp.server import _get_bridge, mcp


@mcp.tool()
async def inspect_image(ctx: Context, path: str) -> dict:
    """Inspect an image's dimensions, channels, and per-channel statistics.

    Reports width, height, channel names, pixel format, subimage count, and
    detected file format, plus per-channel min/max/avg/stddev over the whole
    image. Works on any format OpenImageIO can read (PNG, EXR, RAT, etc.).

    Args:
        path: Filesystem path to the image, as seen by the Houdini session.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("image.inspect_image", {"path": path})


@mcp.tool()
async def sample_image(ctx: Context, path: str, x: int, y: int) -> dict:
    """Sample the per-channel pixel values at a single (x, y) coordinate.

    The coordinate is bounds-checked against the image dimensions.

    Args:
        path: Filesystem path to the image, as seen by the Houdini session.
        x: Pixel column (0-based).
        y: Pixel row (0-based).
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "image.sample_image", {"path": path, "x": x, "y": y}
    )


@mcp.tool()
async def image_region_stats(
    ctx: Context, path: str, x: int, y: int, width: int, height: int
) -> dict:
    """Compute per-channel min/max/avg over a rectangular image region.

    The region is clamped to the image bounds, and the clamped region of
    interest is echoed back in the result.

    Args:
        path: Filesystem path to the image, as seen by the Houdini session.
        x: Region origin column (0-based).
        y: Region origin row (0-based).
        width: Region width in pixels.
        height: Region height in pixels.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "image.image_region_stats",
        {"path": path, "x": x, "y": y, "width": width, "height": height},
    )
