"""VAT / spatial-atlas handlers for FXHoudini-MCP.

Tools for the SideFX Labs VAT -> UE workflow:
  * vat.bake_attribute_to_spatial_atlas  — bake a point attribute over a frame
    range into a spatial flipbook atlas, cooked directly from live geometry.
  * vat.reshape_to_spatial_atlas         — reshape an existing vertex-index VAT
    texture (EXR) into a spatial flipbook atlas.
  * vat.get_vat_layout                   — introspect a Labs VAT ROP's texture
    layout + vertex-index<->texel mapping.
  * vat.sample_vertex                    — read one vertex's VAT texel across
    frames and report frozen-vs-animated.

Image I/O uses OpenImageIO (ships in Houdini's Python). numpy drives the
vectorized atlas math. Both are required (no pure-python fallback); a clear
error is raised if either is missing.

Layered authorship: the shared helpers below (OIIO read/write, gutter fill,
Houdini->UE axis convention + normal encode, frame-list / tile resolution) are
the correctness-critical, cross-tool pieces. The four tool handlers call these
helpers and must not redefine them.
"""

from __future__ import annotations

# Built-in
import contextlib
import logging
import math
import os

# Third-party
import hou
import numpy as np

# Internal
from fxhoudinimcp_server.dispatcher import register_handler

logger = logging.getLogger(__name__)


###### Shared helpers (OIIO image IO, atlas math, encode conventions)

def _oiio():
    """Import OpenImageIO lazily, raising a clear error if unavailable."""
    try:
        import OpenImageIO as oiio

        return oiio
    except Exception as exc:
        raise RuntimeError(
            f"OpenImageIO is not available in this Houdini Python: {exc}"
        ) from exc


def _round_list(values, ndigits: int = 6) -> list:
    """Round an iterable of numbers; non-numerics pass through (JSON-safe)."""
    rounded = []
    for value in values:
        try:
            rounded.append(round(float(value), ndigits))
        except (TypeError, ValueError):
            rounded.append(value)
    return rounded


def _read_exr(path: str):
    """Read an image into a float32 HxWxC numpy array + channel names (OIIO)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    oiio = _oiio()
    inp = oiio.ImageInput.open(path)
    if inp is None:
        raise ValueError(
            f"OpenImageIO could not open image '{path}': {oiio.geterror()}"
        )
    try:
        spec = inp.spec()
        pixels = inp.read_image(format=oiio.FLOAT)
        channels = list(spec.channelnames)
        height, width, nchannels = spec.height, spec.width, spec.nchannels
    finally:
        inp.close()
    if pixels is None:
        raise ValueError(f"OpenImageIO could not read pixels from '{path}'")
    arr = np.asarray(pixels, dtype=np.float32).reshape(height, width, nchannels)
    return arr, channels


def _write_exr(path: str, pixels, channel_names=None, compression: str = "zip") -> None:
    """Write a float32 HxWxC numpy array to an EXR via OIIO ImageOutput."""
    oiio = _oiio()
    arr = np.ascontiguousarray(pixels, dtype=np.float32)
    if arr.ndim != 3:
        raise ValueError(f"pixels must be HxWxC, got shape {arr.shape!r}")
    h, w, c = arr.shape
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    spec = oiio.ImageSpec(w, h, c, oiio.FLOAT)
    if compression:
        spec.attribute("compression", compression)
    if channel_names and len(channel_names) == c:
        spec.channelnames = tuple(channel_names)
    out = oiio.ImageOutput.create(path)
    if out is None:
        raise ValueError(
            f"OpenImageIO could not create output '{path}': {oiio.geterror()}"
        )
    if not out.open(path, spec):
        err = out.geterror()
        out.close()
        raise ValueError(f"OpenImageIO could not open output '{path}': {err}")
    try:
        if not out.write_image(arr):
            raise ValueError(
                f"OpenImageIO failed to write '{path}': {out.geterror()}"
            )
    finally:
        out.close()


def _atlas_fill_gutter(atlas, x0: int, y0: int, pad: int, inner_w: int, inner_h: int) -> None:
    """Edge-replicate a ``pad``-px gutter around a tile's inner rect.

    The tile's full rect starts at ``(x0, y0)``; the inner rect's top-left pixel
    is at ``(x0+pad, y0+pad)`` with size ``(inner_h, inner_w)``. Copies the inner
    edge rows/cols outward into the pad border (corners included) so bilinear
    sampling near a tile edge clamps instead of bleeding black or a neighbour.
    Supports pad>1 (every gutter pixel takes its nearest inner edge value).
    """
    if pad <= 0:
        return
    ix0 = x0 + pad
    iy0 = y0 + pad
    ix1 = ix0 + inner_w  # exclusive
    iy1 = iy0 + inner_h  # exclusive
    tile_full_h = inner_h + 2 * pad
    # Top/bottom borders over the inner column span (replicate inner edge row).
    for p in range(1, pad + 1):
        atlas[iy0 - p, ix0:ix1, :] = atlas[iy0, ix0:ix1, :]
        atlas[iy1 - 1 + p, ix0:ix1, :] = atlas[iy1 - 1, ix0:ix1, :]
    # Left/right borders over the FULL tile height (fills corners too).
    for p in range(1, pad + 1):
        atlas[y0:y0 + tile_full_h, ix0 - p, :] = atlas[y0:y0 + tile_full_h, ix0, :]
        atlas[y0:y0 + tile_full_h, ix1 - 1 + p, :] = atlas[y0:y0 + tile_full_h, ix1 - 1, :]


def _encode_normal(n_arr, axis_convert: bool = True, x_sign: float = 1.0, y_sign: float = 1.0):
    """Encode raw Houdini-space normals (M,3) to UE-convention [0,1] RGB.

    axis_convert maps Houdini Y-up ``(x, y, z)`` to UE Z-up local ``(x, z, y)``.
    x_sign / y_sign flip the resulting X / Y. Output = ``clip(n*0.5+0.5, 0, 1)``
    after normalization. Returns float32 (M,3).
    """
    n = np.asarray(n_arr, dtype=np.float32).reshape(-1, 3)
    if axis_convert:
        out = np.stack([n[:, 0] * x_sign, n[:, 2] * y_sign, n[:, 1]], axis=-1)
    else:
        out = np.stack([n[:, 0] * x_sign, n[:, 1] * y_sign, n[:, 2]], axis=-1)
    length = np.linalg.norm(out, axis=-1, keepdims=True)
    out = out / np.maximum(length, 1e-8)
    return np.clip(out * 0.5 + 0.5, 0.0, 1.0).astype(np.float32)


def _decode_halfunit(enc_arr):
    """Inverse of ``*0.5+0.5``: map [0,1] encoded vectors back to [-1,1]."""
    return np.asarray(enc_arr, dtype=np.float32) * 2.0 - 1.0


def _resolve_frame_list(frames) -> list:
    """Normalize a frames spec to an explicit list of float frame numbers.

    None     -> every integer frame in the playbar range (inclusive).
    [a, b]   -> inclusive integer range (handles a>b too).
    [a,b,inc]-> stepped, inclusive of the endpoint within fp tolerance.
    other    -> passthrough as floats (explicit frame list).
    """
    if frames is None:
        start, end = hou.playbar.playbackRange()
        start_i, end_i = int(round(start)), int(round(end))
        return [float(f) for f in range(start_i, end_i + 1)]
    seq = list(frames)
    if len(seq) == 2:
        a, b = int(round(seq[0])), int(round(seq[1]))
        step = 1 if b >= a else -1
        return [float(f) for f in range(a, b + step, step)]
    if len(seq) == 3:
        a, b, inc = float(seq[0]), float(seq[1]), float(seq[2])
        if inc == 0:
            raise ValueError("frame increment must be non-zero")
        count = int(math.floor((b - a) / inc + 1e-9)) + 1
        return [round(a + k * inc, 6) for k in range(max(count, 0))]
    return [float(f) for f in seq]


def _auto_tiles(frame_count: int, tiles_x=None, tiles_y=None):
    """Resolve atlas tile grid; near-square if unspecified. Must cover all frames."""
    n = max(int(frame_count), 1)
    if tiles_x and tiles_y:
        tx, ty = int(tiles_x), int(tiles_y)
    elif tiles_x:
        tx = int(tiles_x)
        ty = int(math.ceil(n / tx))
    elif tiles_y:
        ty = int(tiles_y)
        tx = int(math.ceil(n / ty))
    else:
        tx = int(math.ceil(math.sqrt(n)))
        ty = int(math.ceil(n / tx))
    if tx * ty < n:
        raise ValueError(
            f"tile grid {tx}x{ty} ({tx * ty}) cannot hold {n} frames"
        )
    return tx, ty


###### vat.bake_attribute_to_spatial_atlas

def bake_attribute_to_spatial_atlas(
    node_path,
    out_path,
    grid_cols,
    grid_rows,
    frames=None,
    attrib_name="N",
    index_mode="ptnum",
    encode="normal_halfunit",
    components=None,
    tiles_x=None,
    tiles_y=None,
    pad=1,
    flip_v=False,
    axis_convert=True,
    normal_x_sign=1.0,
    normal_y_sign=1.0,
    compression="zip",
):
    """Bake a point attribute over a frame range into a spatial flipbook atlas EXR.

    Each frame is cooked from the live SOP and written into one tile of a tiled
    atlas image. The atlas layout is spatial: point i at grid position
    (col, row) maps directly to pixel (col, row) inside the tile, so the
    resulting texture can be sampled in UE by UV + flipbook frame index without
    any vertex-index indirection.

    Encode modes:

    * ``"normal_halfunit"`` — encodes a 3-component normal attribute into
      a 3-channel [0, 1] half-unit image using ``_encode_normal``.  By default
      performs a Houdini->UE axis conversion (Y-up -> Z-up) and packs as
      ``v*0.5 + 0.5``.
    * ``"scalar"`` — writes a single float component (default component 0,
      or ``components[0]``). Produces a 1-channel atlas.
    * ``"raw"`` — writes the raw float components verbatim.  ``components``
      selects which tuple indices to include; if omitted all components of the
      attribute are written.

    Gutter padding (``pad`` pixels, edge-replicate) is added around each tile
    to prevent bilinear-filter bleed between frames in UE flipbooks.

    Topology must be stable across the baked frames (same point count, same
    point ordering) — UV coordinates and pixel mapping are computed only on
    the first cooked frame.
    """
    # 1. Resolve node and frame list
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path!r}")

    grid_cols = int(grid_cols)
    grid_rows = int(grid_rows)
    frame_list = _resolve_frame_list(frames)
    frame_count = len(frame_list)
    if frame_count == 0:
        raise ValueError("frame_list is empty — nothing to bake.")

    # 2. Tile layout
    tiles_x, tiles_y = _auto_tiles(frame_count, tiles_x, tiles_y)

    inner_w = grid_cols
    inner_h = grid_rows
    pad = int(pad)
    tile_full_w = inner_w + 2 * pad
    tile_full_h = inner_h + 2 * pad
    atlas_w = tiles_x * tile_full_w
    atlas_h = tiles_y * tile_full_h

    # 3. Validate encode mode
    valid_encode_modes = ("normal_halfunit", "scalar", "raw")
    if encode not in valid_encode_modes:
        raise ValueError(
            f"Unknown encode mode {encode!r}. Valid modes: {valid_encode_modes}"
        )

    # A first cook gives tuple_size and npoints; also needed for uv mapping.
    original_frame = hou.frame()
    try:
        hou.setFrame(frame_list[0])
        geo0 = node.geometry()
        if geo0 is None:
            raise ValueError(
                f"Node has no geometry at frame {frame_list[0]}: {node_path!r}"
            )

        pt_attrib0 = geo0.findPointAttrib(attrib_name)
        if pt_attrib0 is None:
            raise ValueError(
                f"Point attribute {attrib_name!r} not found on {node_path!r} "
                f"at frame {frame_list[0]}."
            )
        tuple_size = pt_attrib0.size()
        npoints = int(geo0.intrinsicValue("pointcount"))

        if encode == "normal_halfunit":
            C = 3
        elif encode == "scalar":
            C = 1
        else:  # "raw"
            C = len(components) if components is not None else tuple_size

        # 4. Allocate atlas
        atlas = np.zeros((atlas_h, atlas_w, C), dtype=np.float32)
        if C == 4:
            atlas[..., 3] = 1.0  # default alpha=1 for 4-channel

        # 5. Precompute per-point pixel coordinates (once; topology stable)
        if index_mode == "ptnum":
            expected = grid_cols * grid_rows
            if npoints != expected:
                raise ValueError(
                    f"index_mode='ptnum' requires npoints == grid_cols * grid_rows "
                    f"({grid_cols} * {grid_rows} = {expected}), "
                    f"but node has {npoints} points."
                )
            indices = np.arange(npoints, dtype=np.int32)
            px_arr = indices % grid_cols
            py_arr = indices // grid_cols

        elif index_mode == "uv":
            uv_attrib = geo0.findPointAttrib("uv")
            if uv_attrib is None:
                raise ValueError(
                    f"index_mode='uv' requires a 'uv' point attribute on {node_path!r}."
                )
            uv_tuple_size = uv_attrib.size()  # typically 2 or 3
            uv_flat = np.asarray(
                geo0.pointFloatAttribValues("uv"), dtype=np.float32
            ).reshape(-1, uv_tuple_size)
            u = uv_flat[:, 0]
            v = uv_flat[:, 1]
            px_arr = np.clip(
                np.round(u * (grid_cols - 1)).astype(np.int32), 0, grid_cols - 1
            )
            py_arr = np.clip(
                np.round(v * (grid_rows - 1)).astype(np.int32), 0, grid_rows - 1
            )

        else:
            raise ValueError(
                f"Unknown index_mode {index_mode!r}. Valid modes: ('ptnum', 'uv')"
            )

        if flip_v:
            py_arr = (grid_rows - 1) - py_arr

        # 6. Cook loop
        for k, f in enumerate(frame_list):
            hou.setFrame(f)
            geo = node.geometry()
            if geo is None:
                raise ValueError(f"Node has no geometry at frame {f}: {node_path!r}")

            raw_flat = geo.pointFloatAttribValues(attrib_name)
            arr = np.asarray(raw_flat, dtype=np.float32).reshape(-1, tuple_size)

            if encode == "normal_halfunit":
                vals = _encode_normal(
                    arr[:, :3], axis_convert, normal_x_sign, normal_y_sign
                )  # (M, 3)
            elif encode == "scalar":
                comp = int(components[0]) if components else 0
                vals = arr[:, comp:comp + 1]  # (M, 1)
            else:  # "raw"
                vals = (
                    arr[:, [int(c) for c in components]]
                    if components is not None
                    else arr
                )

            tile_x = k % tiles_x
            tile_y = k // tiles_x
            x0 = tile_x * tile_full_w
            y0 = tile_y * tile_full_h

            atlas[y0 + pad + py_arr, x0 + pad + px_arr, 0:vals.shape[1]] = vals
            _atlas_fill_gutter(atlas, x0, y0, pad, inner_w, inner_h)

            logger.debug(
                "bake_attribute_to_spatial_atlas: frame %s (tile %d/%d) done",
                f, k + 1, frame_count,
            )

    finally:
        hou.setFrame(original_frame)

    # 7. Write EXR
    _write_exr(out_path, atlas, compression=compression)
    logger.info(
        "bake_attribute_to_spatial_atlas: wrote %dx%d atlas to %r",
        atlas_w, atlas_h, out_path,
    )

    # 8. Return result dict
    return {
        "node_path": node_path,
        "out_path": out_path,
        "atlas_width": atlas_w,
        "atlas_height": atlas_h,
        "channels": C,
        "tiles_x": tiles_x,
        "tiles_y": tiles_y,
        "frame_count": frame_count,
        "frames": [frame_list[0], frame_list[-1]],
        "inner_w": inner_w,
        "inner_h": inner_h,
        "pad": pad,
        "encode": encode,
        "index_mode": index_mode,
        "axis_convert": bool(axis_convert),
        "npoints": npoints,
        "attrib": attrib_name,
    }


###### vat.reshape_to_spatial_atlas

def reshape_to_spatial_atlas(
    src_path,
    out_path,
    grid_cols,
    grid_rows,
    frames,
    vat_width=None,
    rows_per_frame=None,
    frame_start_row=0,
    src_channels=None,
    decode="normal_halfunit",
    axis_convert=True,
    normal_x_sign=1.0,
    normal_y_sign=1.0,
    flip_v=False,
    tiles_x=15,
    tiles_y=10,
    pad=1,
    compression="zip",
):
    """Reshape an existing vertex-index VAT EXR into a spatial UV flipbook atlas.

    A baked Labs VAT stores data in vertex-index layout: each row holds
    consecutive point data packed left-to-right, with frames stacked vertically.
    This function re-arranges that layout into a spatial flipbook atlas where
    each tile contains one frame's worth of data arranged spatially (pixel
    column = grid X, pixel row = grid Z/Y), matching the geometry UV layout so
    the atlas can be directly sampled by UV0 in a real-time shader.

    The ``decode`` parameter controls per-texel post-processing:
    - ``"normal_halfunit"``: assumes the VAT stores normals encoded as
      ``v*0.5+0.5`` in Houdini space, decodes them back to raw vectors, then
      re-encodes them in Unreal Engine convention (Y-up -> Z-up axis swap,
      optional sign flips) via ``_encode_normal``.
    - ``"raw"``: clips values to [0,1] and writes them as-is (use for position
      or custom float data that is already in the right convention).

    Each tile in the atlas is padded by ``pad`` pixels of edge-replicated gutter
    on all four sides via ``_atlas_fill_gutter``, which prevents bilinear bleed
    between tiles at runtime.
    """
    if not os.path.isfile(src_path):
        raise FileNotFoundError(f"Source VAT EXR not found: {src_path!r}")

    # 1. Read source EXR
    src, ch_names = _read_exr(src_path)
    vat_h, vat_w_actual = src.shape[0], src.shape[1]

    # 2. Validate / resolve vat_width
    vat_width = vat_width if vat_width is not None else vat_w_actual
    if vat_width != src.shape[1]:
        raise ValueError(
            f"vat_width={vat_width} does not match actual EXR width "
            f"{src.shape[1]} for '{src_path}'."
        )

    # 3. Resolve rows_per_frame
    points_per_frame = int(grid_cols) * int(grid_rows)
    rows_per_frame = (
        rows_per_frame
        if rows_per_frame is not None
        else math.ceil(points_per_frame / vat_width)
    )

    # 4. Validate source height (coarse: assumes rows_per_frame is consistent)
    required_height = frame_start_row + rows_per_frame * frames
    if vat_h < required_height:
        raise ValueError(
            f"Source EXR height {vat_h} is too small: need at least "
            f"{required_height} rows (frame_start_row={frame_start_row} + "
            f"rows_per_frame={rows_per_frame} * frames={frames})."
        )

    # 5. Resolve src_channels
    src_channels = src_channels if src_channels is not None else [0, 1, 2]
    max_ch = max(src_channels)
    if src.shape[2] < max_ch + 1:
        raise ValueError(
            f"src_channels index {max_ch} exceeds source channel count "
            f"{src.shape[2]} for '{src_path}'."
        )
    if decode == "normal_halfunit" and len(src_channels) < 3:
        raise ValueError(
            f"decode='normal_halfunit' needs at least 3 src_channels "
            f"(got {list(src_channels)})."
        )

    # 6. Build atlas
    inner_w = int(grid_cols)
    inner_h = int(grid_rows)
    tile_full_w = inner_w + 2 * pad
    tile_full_h = inner_h + 2 * pad
    atlas_w = tiles_x * tile_full_w
    atlas_h = tiles_y * tile_full_h

    if tiles_x * tiles_y < frames:
        raise ValueError(
            f"Atlas tile count {tiles_x}x{tiles_y}={tiles_x * tiles_y} is "
            f"insufficient for {frames} frames."
        )

    atlas = np.zeros((atlas_h, atlas_w, 4), dtype=np.float32)
    atlas[..., 3] = 1.0

    # 7. Precompute per-point VAT and pixel coordinates
    ids = np.arange(points_per_frame, dtype=np.int64)
    vat_x = ids % vat_width                      # column in source VAT
    vat_row = ids // vat_width                    # row offset within one frame
    px = ids % inner_w                            # pixel column in tile
    py = ids // inner_w                           # pixel row in tile
    if flip_v:
        py = (inner_h - 1) - py

    # Tight bound check: the actual deepest source row we will read.
    max_src_y = int((frames - 1) * rows_per_frame + frame_start_row + int(vat_row.max()))
    if max_src_y >= vat_h:
        raise ValueError(
            f"Computed deepest source row {max_src_y} exceeds EXR height "
            f"{vat_h}; rows_per_frame={rows_per_frame} is too small for "
            f"{points_per_frame} points at vat_width={vat_width}."
        )

    # 8. Fill each frame tile
    for f in range(frames):
        tile_x = f % tiles_x
        tile_y = f // tiles_x
        x0 = tile_x * tile_full_w
        y0 = tile_y * tile_full_h

        src_y = f * rows_per_frame + frame_start_row + vat_row  # (points,)
        enc = src[src_y, vat_x][:, src_channels]                # (points, len)

        if decode == "normal_halfunit":
            n = _decode_halfunit(enc[:, :3])
            vals = _encode_normal(n, axis_convert, normal_x_sign, normal_y_sign)
        else:  # "raw"
            vals = np.clip(enc, 0.0, 1.0) if enc.shape[1] <= 4 else enc

        atlas[y0 + pad + py, x0 + pad + px, 0:vals.shape[1]] = vals
        _atlas_fill_gutter(atlas, x0, y0, pad, inner_w, inner_h)

    # 9. Write output EXR
    _write_exr(out_path, atlas, compression=compression)
    logger.info(
        "reshape_to_spatial_atlas: wrote %dx%d atlas (%d tiles, %d frames) -> %s",
        atlas_w, atlas_h, tiles_x * tiles_y, frames, out_path,
    )

    # 10. Return result dict
    return {
        "src_path": src_path,
        "out_path": out_path,
        "atlas_width": int(atlas_w),
        "atlas_height": int(atlas_h),
        "tiles_x": int(tiles_x),
        "tiles_y": int(tiles_y),
        "frames": int(frames),
        "rows_per_frame": int(rows_per_frame),
        "vat_width": int(vat_width),
        "frame_start_row": int(frame_start_row),
        "inner_w": int(inner_w),
        "inner_h": int(inner_h),
        "pad": int(pad),
        "decode": decode,
        "axis_convert": bool(axis_convert),
        "src_channels": list(src_channels),
    }


###### vat.get_vat_layout

def get_vat_layout(node_path, num_points=None):
    """Introspect a Labs Vertex Animation Textures 3.0 ROP to report its
    texture layout and vertex-index-to-texel mapping formula.

    Discovers all parameters by walking the node, then tries prioritized
    candidate lists to locate key values (width, height, mode/method, frame
    range, flags, and output paths). Every discovered value records which parm
    name was chosen (and the candidates tried) so the caller can cross-check
    against the live node. Missing values are reported as None rather than
    raising, and the full ``all_parms`` snapshot is always returned.
    """
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path!r}")

    node_type_name = node.type().name()
    node_type_full = node.type().nameWithCategory()

    # 1. Snapshot ALL parms: {name: safe_eval_result}
    def _safe_eval(parm):
        """Evaluate a parm, returning None on any exception (JSON-safe)."""
        try:
            val = parm.eval()
            if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)):
                try:
                    return list(val)
                except Exception:
                    return str(val)
            if isinstance(val, (int, float, str, bool)):
                return val
            return str(val)
        except Exception:
            return None

    all_parms = {}
    try:
        for parm in node.parms():
            all_parms[parm.name()] = _safe_eval(parm)
    except Exception as exc:
        logger.warning("Could not enumerate parms on %s: %s", node_path, exc)

    # 2. Helpers to pick the first candidate parm that exists and evaluates
    def _pick(candidates):
        for name in candidates:
            p = node.parm(name)
            if p is not None:
                val = _safe_eval(p)
                if val is not None:
                    return name, val
        return None, None

    def _pick_with_note(candidates):
        parm_name, value = _pick(candidates)
        return parm_name, value, list(candidates)

    # 3. Discover texture width & height
    w_parm, w_val, w_tried = _pick_with_note(
        ["maxtexturewidth", "texturewidth", "width", "tex_width", "resx",
         "xres", "resolutionx"]
    )
    h_parm, h_val, h_tried = _pick_with_note(
        ["textureheight", "height", "tex_height", "resy", "maxheight",
         "yres", "resolutiony"]
    )
    tex_width = int(w_val) if w_val is not None else None
    tex_height = int(h_val) if h_val is not None else None
    tex_dims_source = "parm" if tex_width is not None else None

    # 4. Discover mode / method
    mode_parm, mode_raw, mode_tried = _pick_with_note(
        ["method", "mode", "exportmode", "vatmode", "animtype", "type"]
    )
    mode_label = None
    if mode_parm is not None:
        p = node.parm(mode_parm)
        if p is not None:
            try:
                tmpl = p.parmTemplate()
                if hasattr(tmpl, "menuLabels"):
                    labels = tmpl.menuLabels()
                    items = tmpl.menuItems()
                    if isinstance(mode_raw, int) and mode_raw < len(labels):
                        mode_label = labels[mode_raw]
                    elif isinstance(mode_raw, str):
                        try:
                            idx = list(items).index(mode_raw)
                            mode_label = labels[idx]
                        except (ValueError, IndexError):
                            mode_label = mode_raw
            except Exception as exc:
                logger.debug("Could not read menu labels for %s: %s", mode_parm, exc)

    # 5. Discover frame range
    f_start = f_end = f_inc = None
    f_parm_used = None
    f_tried = []

    p_f = node.parm("f1") or node.parm("f")
    if p_f is None:
        try:
            pt = node.parmTuple("f")
            if pt is not None:
                vals = [_safe_eval(c) for c in pt]
                if len(vals) >= 2 and vals[0] is not None and vals[1] is not None:
                    f_start = float(vals[0])
                    f_end = float(vals[1])
                    f_inc = float(vals[2]) if len(vals) > 2 and vals[2] is not None else 1.0
                    f_parm_used = "f (parmTuple)"
        except Exception:
            pass
    else:
        p1 = node.parm("f1")
        p2 = node.parm("f2")
        p3 = node.parm("f3")
        if p1 and p2:
            v1 = _safe_eval(p1)
            v2 = _safe_eval(p2)
            if v1 is not None and v2 is not None:
                f_start = float(v1)
                f_end = float(v2)
                v3 = _safe_eval(p3) if p3 else None
                f_inc = float(v3) if v3 is not None else 1.0
                f_parm_used = "f1/f2/f3"

    if f_start is None:
        for cand in ["framerange", "trange", "startframe"]:
            f_tried.append(cand)
            p = node.parm(cand)
            if p is not None:
                val = _safe_eval(p)
                if val is not None:
                    f_start = float(val) if isinstance(val, (int, float)) else None
                    f_parm_used = cand
                    break

    if f_end is None:
        for cand in ["endframe", "frameend"]:
            p = node.parm(cand)
            if p is not None:
                val = _safe_eval(p)
                if val is not None:
                    f_end = float(val)
                    break

    pb_start = pb_end = None
    try:
        pb_start, pb_end = hou.playbar.playbackRange()
        pb_start = float(pb_start)
        pb_end = float(pb_end)
    except Exception:
        pass

    if f_start is None and pb_start is not None:
        f_start = pb_start
        f_parm_used = "hou.playbar.playbackRange (fallback)"
    if f_end is None and pb_end is not None:
        f_end = pb_end

    fps = None
    with contextlib.suppress(Exception):
        fps = float(hou.fps())

    frame_range_dict = {
        "parm": f_parm_used,
        "start": f_start,
        "end": f_end,
        "inc": f_inc if f_inc is not None else 1.0,
        "candidates_tried": f_tried,
        "playbar_range": [pb_start, pb_end] if pb_start is not None else None,
    }

    frame_count = None
    if f_start is not None and f_end is not None and f_inc:
        frame_count = int(math.floor((f_end - f_start) / f_inc)) + 1

    # 6. Discover num_points from node's input / soppath parm
    num_points_note = None
    if num_points is None:
        sop_node = None
        for sop_parm_name in ["soppath", "sop", "sopnode", "input"]:
            p = node.parm(sop_parm_name)
            if p is not None:
                val = _safe_eval(p)
                if val and isinstance(val, str):
                    sop_node = hou.node(val)
                    if sop_node is not None:
                        break
        if sop_node is None:
            try:
                inputs = node.inputs()
                if inputs:
                    sop_node = inputs[0]
            except Exception:
                pass
        if sop_node is not None:
            try:
                geo = sop_node.geometry()
                if geo is not None:
                    num_points = int(geo.intrinsicValue("pointcount"))
                    num_points_note = f"auto-read from {sop_node.path()}"
                else:
                    num_points_note = f"input {sop_node.path()} has no geometry (not cooked?)"
            except Exception as exc:
                num_points_note = f"could not read geometry from input: {exc}"
        else:
            num_points_note = "no input node or soppath parm found; pass num_points explicitly"
    else:
        num_points = int(num_points)
        num_points_note = "provided by caller"

    # 7. rows_per_frame + texel mapping are computed in step 11, after the
    #    texture width is resolved (which may require reading an output image).
    rows_per_frame = None
    texel_mapping = None

    # 8. Flags: compressed normals in pos alpha & spare color.
    #    Labs VAT 3.0 names these per-mode: packnorm_soft / packnorm_fluid (the
    #    "compress normals into position alpha" toggle) and addsparecol (the
    #    "export spare Cd2/Alpha2" toggle).
    cn_parm, cn_val, cn_tried = _pick_with_note(
        ["packnorm_soft", "packnorm_fluid", "packnormal", "exportnormal",
         "compressednormals", "normalinposalpha", "addnormalstoposa",
         "storenormalinposalpha", "normalsinpos"]
    )
    sc_parm, sc_val, sc_tried = _pick_with_note(
        ["addsparecol", "exportcd2", "sparecolor", "usesparecolor", "cd2",
         "exportcolor2", "color2", "colortwo"]
    )

    def _bool_hint(v):
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return bool(v)
        if isinstance(v, str):
            return v.lower() in ("1", "true", "on", "yes")
        return None

    flags = {
        "compressed_normals_in_pos_alpha": {
            "parm": cn_parm,
            "value": cn_val,
            "bool_hint": _bool_hint(cn_val),
            "candidates_tried": cn_tried,
        },
        "spare_color": {
            "parm": sc_parm,
            "value": sc_val,
            "bool_hint": _bool_hint(sc_val),
            "candidates_tried": sc_tried,
        },
    }

    # 9. Discover output paths
    _ROLE_HINTS = {
        "pos": "position_offsets_exr",
        "rot": "rotation_exr",
        "col2": "spare_color_second_color_exr",
        "col": "color_mask_exr",
        "n": "normal_exr",
        "normal": "normal_exr",
        "geo": "reference_mesh_fbx",
        "mesh": "reference_mesh_fbx",
        "fbx": "reference_mesh_fbx",
        "alembic": "reference_mesh_abc",
    }

    def _guess_role(parm_name, path_val):
        lower = (parm_name + "_" + str(path_val)).lower()
        for keyword, role in _ROLE_HINTS.items():
            if keyword in lower:
                return role
        return "unknown"

    outputs = []
    seen_output_parms = set()

    _OUTPUT_CANDIDATES = [
        "sopoutput", "picture", "output", "filename",
        "outputfile", "out", "outputpath",
    ]
    for cand in _OUTPUT_CANDIDATES:
        p = node.parm(cand)
        if p is not None and cand not in seen_output_parms:
            val = _safe_eval(p)
            if val and isinstance(val, str):
                seen_output_parms.add(cand)
                unexpanded = val
                with contextlib.suppress(Exception):
                    unexpanded = p.unexpandedString()
                outputs.append({
                    "parm": cand,
                    "path": val,
                    "unexpanded": unexpanded,
                    "exists": os.path.isfile(val),
                    "role": _guess_role(cand, val),
                })

    for parm in node.parms():
        if parm.name().startswith("path_") and parm.name() not in seen_output_parms:
            val = _safe_eval(parm)
            if val and isinstance(val, str):
                seen_output_parms.add(parm.name())
                unexpanded = val
                with contextlib.suppress(Exception):
                    unexpanded = parm.unexpandedString()
                outputs.append({
                    "parm": parm.name(),
                    "path": val,
                    "unexpanded": unexpanded,
                    "exists": os.path.isfile(val),
                    "role": _guess_role(parm.name(), val),
                })

    # Catch exotic string parms whose value looks like a file path.
    for parm in node.parms():
        if parm.name() in seen_output_parms:
            continue
        try:
            tmpl = parm.parmTemplate()
            if tmpl.type() != hou.parmTemplateType.String:
                continue
        except Exception:
            continue
        val = _safe_eval(parm)
        if not (val and isinstance(val, str)):
            continue
        tail = val.replace("\\", "/").split("/")[-1]
        if ("/" in val or "\\" in val) and "." in tail:
            seen_output_parms.add(parm.name())
            outputs.append({
                "parm": parm.name(),
                "path": val,
                "unexpanded": val,
                "exists": os.path.isfile(val),
                "role": _guess_role(parm.name(), val),
            })

    # 11. Resolve texture dimensions. Labs VAT 3.0 computes the texture size
    #     automatically and does NOT expose it as a simple parm, so when the
    #     candidate parms miss, fall back to reading an already-baked output
    #     image's real dimensions. Then compute rows_per_frame + texel mapping.
    if tex_width is None:
        for o in outputs:
            p = o.get("path", "")
            if o.get("exists") and p.lower().endswith((".exr", ".tif", ".tiff", ".png", ".rat")):
                try:
                    oiio = _oiio()
                    inp = oiio.ImageInput.open(p)
                    if inp is not None:
                        sp = inp.spec()
                        tex_width, tex_height = int(sp.width), int(sp.height)
                        inp.close()
                        tex_dims_source = f"read from output image {p}"
                        break
                except Exception as exc:
                    logger.debug("Could not read dims from %s: %s", p, exc)

    if tex_width and num_points and tex_width > 0:
        rows_per_frame = math.ceil(num_points / tex_width)
        texel_mapping = {
            "formula": (
                "vat_x = vertex_index % W; "
                "vat_row = vertex_index // W; "
                "vat_y = frame_index * rows_per_frame + frame_start_row + vat_row"
            ),
            "W": tex_width,
            "rows_per_frame": rows_per_frame,
            "frame_start_row": 0,
            "note": (
                "frame_index is 0-based (frame 0 = first frame in the baked range). "
                "frame_start_row is 0 for the standard SideFX layout."
            ),
        }

    # 12. Assemble and return
    return {
        "node_path": node_path,
        "node_type": node_type_name,
        "node_type_full": node_type_full,
        "mode": {
            "parm": mode_parm,
            "raw": mode_raw,
            "label": mode_label,
            "candidates_tried": mode_tried,
        },
        "texture": {
            "width": {"parm": w_parm, "value": tex_width, "candidates_tried": w_tried},
            "height": {"parm": h_parm, "value": tex_height, "candidates_tried": h_tried},
            "source": tex_dims_source,
        },
        "frame_range": frame_range_dict,
        "frame_count": frame_count,
        "fps": fps,
        "num_points": num_points,
        "num_points_note": num_points_note,
        "rows_per_frame": rows_per_frame,
        "texel_mapping": texel_mapping,
        "flags": flags,
        "outputs": outputs,
        "all_parms": all_parms,
    }


###### vat.sample_vertex

def sample_vertex(
    src_path,
    vertex_index=None,
    grid_col=None,
    grid_row=None,
    grid_cols=None,
    frames=None,
    vat_width=None,
    rows_per_frame=None,
    frame_start_row=0,
    channels=None,
    decode="raw",
    frozen_tol=1e-6,
):
    """Sample one vertex's VAT texel across frames and report frozen-vs-animated.

    Reads a VAT EXR directly (no live Houdini cook) and extracts the pixel at
    the texel column/row that corresponds to the requested vertex, for every
    requested frame row.  Computes per-channel range statistics and decides
    whether the data is frozen (all frames agree within ``frozen_tol``) or
    animated.

    Mirrors the ``compare_frames`` / ``verify_animation`` idiom but on a VAT
    texture file rather than live SOP geometry — ideal for confirming that,
    e.g., the pos-alpha channel is static while a spare-color channel animates.

    Texel location: ``vat_x = vertex_index % W``, ``vat_row = vertex_index // W``,
    and for frame ``f``: ``vat_y = f * rows_per_frame + frame_start_row + vat_row``.
    """
    # 1. Resolve flat vertex index
    if vertex_index is not None:
        i = int(vertex_index)
    elif grid_col is not None and grid_row is not None and grid_cols is not None:
        i = int(grid_row) * int(grid_cols) + int(grid_col)
    else:
        raise ValueError(
            "Provide vertex_index OR all three of grid_col, grid_row, and "
            "grid_cols to identify the vertex."
        )

    # 2. Read the EXR
    if not os.path.isfile(src_path):
        raise FileNotFoundError(src_path)
    src, ch_names = _read_exr(src_path)
    vat_h, vat_w_actual, nch = src.shape

    if vat_width is not None:
        vat_width = int(vat_width)
        if vat_width != vat_w_actual:
            raise ValueError(
                f"vat_width={vat_width} does not match actual image width "
                f"{vat_w_actual} for '{src_path}'."
            )
    else:
        vat_width = vat_w_actual

    # 3. Texel column and row-within-frame-band
    vat_x = i % vat_width
    vat_row = i // vat_width

    # 4. Resolve the list of frame indices to sample
    frame_start_row = int(frame_start_row)
    if frames is None:
        if rows_per_frame is None:
            raise ValueError(
                "rows_per_frame is required when frames=None so the maximum "
                "frame count can be inferred from the image height."
            )
        rows_per_frame = int(rows_per_frame)
        available_rows = vat_h - frame_start_row
        if available_rows <= 0:
            raise ValueError(
                f"frame_start_row={frame_start_row} leaves no rows in an image "
                f"of height {vat_h}."
            )
        max_frames = available_rows // rows_per_frame
        if max_frames < 1:
            raise ValueError(
                f"rows_per_frame={rows_per_frame} is larger than the available "
                f"image rows ({available_rows})."
            )
        frame_list = list(range(max_frames))
    elif (
        len(frames) == 2
        and isinstance(frames[0], (int, float))
        and isinstance(frames[1], (int, float))
    ):
        frame_list = list(range(int(frames[0]), int(frames[1]) + 1))
    else:
        frame_list = [int(f) for f in frames]

    if rows_per_frame is None:
        raise ValueError(
            "rows_per_frame is required to compute the image row for each frame "
            "(vat_y = frame * rows_per_frame + frame_start_row + vat_row)."
        )
    rows_per_frame = int(rows_per_frame)

    # 5. Resolve channel selection
    if channels is None:
        channel_indices = list(range(nch))
    else:
        channel_indices = [int(c) for c in channels]
        for c in channel_indices:
            if c < 0 or c >= nch:
                raise ValueError(
                    f"Channel index {c} is out of range for an image with "
                    f"{nch} channels."
                )
    n_channels_sel = len(channel_indices)

    # 6. Sample each frame
    per_frame = []
    values_across_frames = []  # (n_frames, n_channels_sel)
    for f in frame_list:
        vat_y = f * rows_per_frame + frame_start_row + vat_row
        if vat_y < 0 or vat_y >= vat_h:
            raise ValueError(
                f"Frame {f}: computed vat_y={vat_y} is out of range for image "
                f"height {vat_h} (rows_per_frame={rows_per_frame}, "
                f"frame_start_row={frame_start_row}, vat_row={vat_row})."
            )
        pixel = src[vat_y, vat_x]  # (nch,)
        sel = [float(pixel[c]) for c in channel_indices]
        values_across_frames.append(sel)

        entry = {
            "frame": f,
            "vat_x": int(vat_x),
            "vat_y": int(vat_y),
            "values": _round_list(sel),
        }
        if decode == "normal_halfunit":
            decoded = _decode_halfunit(np.array(sel, dtype=np.float32)).tolist()
            entry["decoded"] = _round_list(decoded)
        per_frame.append(entry)

    # 7. Frozen analysis
    if len(values_across_frames) == 0:
        per_channel_range = [0.0] * n_channels_sel
        per_channel_min_vals = [0.0] * n_channels_sel
        per_channel_max_vals = [0.0] * n_channels_sel
        frozen_per_channel = [True] * n_channels_sel
        is_frozen = True
        max_abs_change = 0.0
    else:
        arr = np.array(values_across_frames, dtype=np.float32)  # (F, C)
        ch_min = arr.min(axis=0)
        ch_max = arr.max(axis=0)
        ch_range = ch_max - ch_min
        per_channel_range = _round_list(ch_range.tolist())
        per_channel_min_vals = _round_list(ch_min.tolist())
        per_channel_max_vals = _round_list(ch_max.tolist())
        frozen_per_channel = [bool(r <= frozen_tol) for r in ch_range.tolist()]
        is_frozen = all(frozen_per_channel)
        max_abs_change = float(ch_range.max())

    is_animated = not is_frozen

    return {
        "src_path": src_path,
        "vertex_index": i,
        "vat_x": int(vat_x),
        "vat_row": int(vat_row),
        "channels": channel_indices,
        "channel_names": list(ch_names),
        "frames_checked": frame_list,
        "per_frame": per_frame,
        "is_frozen": is_frozen,
        "is_animated": is_animated,
        "frozen_per_channel": frozen_per_channel,
        "per_channel_range": per_channel_range,
        "per_channel_min": per_channel_min_vals,
        "per_channel_max": per_channel_max_vals,
        "max_abs_change": round(max_abs_change, 6),
        "decode": decode,
    }


###### Registration

register_handler("vat.bake_attribute_to_spatial_atlas", bake_attribute_to_spatial_atlas)
register_handler("vat.reshape_to_spatial_atlas", reshape_to_spatial_atlas)
register_handler("vat.get_vat_layout", get_vat_layout)
register_handler("vat.sample_vertex", sample_vertex)
