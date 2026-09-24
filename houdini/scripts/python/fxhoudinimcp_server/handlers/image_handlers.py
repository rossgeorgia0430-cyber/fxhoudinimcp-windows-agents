"""Image inspection handlers for FXHoudini-MCP.

Provides read-only inspection of rendered images, VAT textures, and EXRs
(dimensions, channels, per-channel statistics, and pixel sampling), and tiles
image sequences into contact sheets, using OpenImageIO, which ships inside
Houdini's Python. OpenImageIO is imported
lazily inside each handler so this module imports cleanly even when OIIO is
unavailable in a given Houdini build.
"""

from __future__ import annotations

# Built-in
import logging
import math
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


###### image.make_contact_sheet

def make_contact_sheet(
    images: list, output_path: str, columns: int = 4, tile_width: int = None
) -> dict:
    """Tile images row-major into one sheet and return a downscaled preview.

    Every tile is resized to the first image's aspect ratio at ``tile_width``
    (default: the first image's own width) and written as RGB, so one call
    turns a flipbook sequence into a single reviewable picture.

    Args:
        images: Image paths, tiled left-to-right then top-to-bottom.
        output_path: Sheet file to write (format from the extension).
        columns: Tiles per row.
        tile_width: Tile width in pixels.

    Returns:
        A dict with the sheet path and layout plus ``image_base64`` /
        ``mime_type`` for an inline preview.
    """
    if not images:
        raise ValueError("images must not be empty")
    if columns < 1:
        raise ValueError(f"columns must be at least 1, got {columns}")
    for path in images:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

    oiio = _oiio()
    from OpenImageIO import ROI, ImageBuf, ImageBufAlgo, ImageSpec

    # Lazy: the viewport module needs hou, which this module avoids at import.
    from fxhoudinimcp_server.handlers.viewport_handlers import _downscale_and_encode

    first = ImageBuf(images[0]).spec()
    width = int(tile_width) if tile_width else first.width
    height = max(1, round(first.height * width / first.width))
    rows = math.ceil(len(images) / columns)
    sheet = ImageBuf(ImageSpec(width * columns, height * rows, 3, oiio.UINT8))
    ImageBufAlgo.zero(sheet)
    for index, path in enumerate(images):
        source = ImageBuf(path)
        order = (0, 1, 2) if source.spec().nchannels >= 3 else (0, 0, 0)
        tile = ImageBufAlgo.resize(
            ImageBufAlgo.channels(source, order), roi=ROI(0, width, 0, height, 0, 1, 0, 3)
        )
        ImageBufAlgo.paste(sheet, (index % columns) * width, (index // columns) * height, 0, 0, tile)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    if not sheet.write(output_path):
        raise ValueError(f"Could not write contact sheet '{output_path}': {sheet.geterror()}")
    image_base64, mime_type = _downscale_and_encode(output_path)
    return {
        "output_path": output_path,
        "images": len(images),
        "columns": columns,
        "rows": rows,
        "tile_size": [width, height],
        "image_base64": image_base64,
        "mime_type": mime_type,
    }


###### Registration

register_handler("image.inspect_image", inspect_image)
register_handler("image.sample_image", sample_image)
register_handler("image.image_region_stats", image_region_stats)
register_handler("image.make_contact_sheet", make_contact_sheet)
