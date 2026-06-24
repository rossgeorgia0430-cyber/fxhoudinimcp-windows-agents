"""Image inspection handlers for FXHoudini-MCP.

Provides read-only inspection of rendered images, VAT textures, and EXRs
(dimensions, channels, per-channel statistics, and pixel sampling) using
OpenImageIO, which ships inside Houdini's Python. OpenImageIO is imported
lazily inside each handler so this module imports cleanly even when OIIO is
unavailable in a given Houdini build.
"""

from __future__ import annotations

# Built-in
import logging
import os

# Internal
from fxhoudinimcp_server.dispatcher import register_handler

logger = logging.getLogger(__name__)


###### OpenImageIO access

def _oiio():
    """Import OpenImageIO lazily, raising a clear error if unavailable.

    Returns:
        The OpenImageIO module.

    Raises:
        RuntimeError: If OpenImageIO cannot be imported in this Python.
    """
    try:
        import OpenImageIO as oiio

        return oiio
    except Exception as exc:
        raise RuntimeError(
            f"OpenImageIO is not available in this Houdini Python: {exc}"
        ) from exc


def _round_list(values, ndigits: int = 6) -> list:
    """Round an iterable of numbers to ``ndigits`` decimal places.

    Non-numeric entries are passed through unchanged so the result stays
    JSON-able even if OIIO ever yields an unexpected value.
    """
    rounded = []
    for value in values:
        try:
            rounded.append(round(float(value), ndigits))
        except (TypeError, ValueError):
            rounded.append(value)
    return rounded


###### image.inspect_image

def inspect_image(path: str) -> dict:
    """Inspect an image file's dimensions, channels, and per-channel stats.

    Opens the image to read its specification, then computes per-channel
    pixel statistics (min/max/avg/stddev) across the whole image.

    Args:
        path: Filesystem path to the image (e.g. .png, .exr, .rat).

    Returns:
        A dict with path, width, height, nchannels, channels, format,
        subimages, file_format, and a per-channel ``stats`` block.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    oiio = _oiio()
    from OpenImageIO import ImageBuf, ImageBufAlgo

    image_input = oiio.ImageInput.open(path)
    if image_input is None:
        raise ValueError(
            f"OpenImageIO could not open image '{path}': {oiio.geterror()}"
        )

    try:
        spec = image_input.spec()

        # Count subimages (multi-part EXRs expose more than one).
        subimages = 1
        try:
            while image_input.seek_subimage(subimages, 0):
                subimages += 1
        except Exception as exc:
            logger.debug("Could not enumerate subimages for '%s': %s", path, exc)

        try:
            file_format = image_input.format_name()
        except Exception as exc:
            logger.debug("Could not read format_name for '%s': %s", path, exc)
            file_format = None
    finally:
        image_input.close()

    stats_block: dict = {}
    buf = ImageBuf(path)
    pixel_stats = ImageBufAlgo.computePixelStats(buf)
    if pixel_stats is None:
        raise ValueError(
            f"OpenImageIO could not compute pixel stats for '{path}': "
            f"{buf.geterror()}"
        )

    stats_block = {
        "min": _round_list(pixel_stats.min),
        "max": _round_list(pixel_stats.max),
        "avg": _round_list(pixel_stats.avg),
        "stddev": _round_list(pixel_stats.stddev),
    }

    return {
        "path": path,
        "width": spec.width,
        "height": spec.height,
        "nchannels": spec.nchannels,
        "channels": list(spec.channelnames),
        "format": str(spec.format),
        "subimages": subimages,
        "file_format": file_format,
        "stats": stats_block,
    }


###### image.sample_image

def sample_image(path: str, x: int, y: int) -> dict:
    """Sample the per-channel pixel values at a single (x, y) coordinate.

    Args:
        path: Filesystem path to the image.
        x: Pixel column (0-based, must be within the image width).
        y: Pixel row (0-based, must be within the image height).

    Returns:
        A dict with path, x, y, channels, and per-channel ``values``.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    _oiio()
    from OpenImageIO import ImageBuf

    x = int(x)
    y = int(y)

    buf = ImageBuf(path)
    spec = buf.spec()

    if x < 0 or x >= spec.width or y < 0 or y >= spec.height:
        raise ValueError(
            f"Sample coordinate ({x}, {y}) is out of range for image "
            f"'{path}' of size {spec.width}x{spec.height}."
        )

    values = buf.getpixel(x, y)

    return {
        "path": path,
        "x": x,
        "y": y,
        "channels": list(spec.channelnames),
        "values": _round_list(values),
    }


###### image.image_region_stats

def image_region_stats(
    path: str, x: int, y: int, width: int, height: int
) -> dict:
    """Compute per-channel min/max/avg over a rectangular region.

    The requested region is clamped to the image bounds before statistics are
    computed, and the clamped region of interest is echoed back in the result.

    Args:
        path: Filesystem path to the image.
        x: Region origin column (0-based).
        y: Region origin row (0-based).
        width: Region width in pixels.
        height: Region height in pixels.

    Returns:
        A dict with path, the echoed ``roi``, channels, and a per-channel
        ``stats`` block (min/max/avg).
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    oiio = _oiio()
    from OpenImageIO import ImageBuf, ImageBufAlgo

    x = int(x)
    y = int(y)
    width = int(width)
    height = int(height)

    buf = ImageBuf(path)
    spec = buf.spec()

    # Clamp the requested rectangle to the image bounds.
    x_begin = max(0, min(x, spec.width))
    y_begin = max(0, min(y, spec.height))
    x_end = max(x_begin, min(x + width, spec.width))
    y_end = max(y_begin, min(y + height, spec.height))

    if x_end <= x_begin or y_end <= y_begin:
        raise ValueError(
            f"Region (x={x}, y={y}, width={width}, height={height}) does not "
            f"overlap image '{path}' of size {spec.width}x{spec.height}."
        )

    roi = oiio.ROI(x_begin, x_end, y_begin, y_end, 0, 1, 0, spec.nchannels)

    # computePixelStats accepts a roi keyword in current OpenImageIO; if a
    # given build lacks it, fall back to cropping a copy to the region first.
    try:
        pixel_stats = ImageBufAlgo.computePixelStats(buf, roi=roi)
    except TypeError:
        cropped = ImageBufAlgo.crop(buf, roi)
        pixel_stats = ImageBufAlgo.computePixelStats(cropped)

    if pixel_stats is None:
        raise ValueError(
            f"OpenImageIO could not compute region stats for '{path}': "
            f"{buf.geterror()}"
        )

    return {
        "path": path,
        "roi": {
            "x": x_begin,
            "y": y_begin,
            "width": x_end - x_begin,
            "height": y_end - y_begin,
        },
        "channels": list(spec.channelnames),
        "stats": {
            "min": _round_list(pixel_stats.min),
            "max": _round_list(pixel_stats.max),
            "avg": _round_list(pixel_stats.avg),
        },
    }


###### Registration

register_handler("image.inspect_image", inspect_image)
register_handler("image.sample_image", sample_image)
register_handler("image.image_region_stats", image_region_stats)
