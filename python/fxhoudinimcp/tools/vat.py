"""MCP tool wrappers for the SideFX Labs VAT -> UE workflow.

These tools turn the hand-written scripts that recur in the VAT smooth-normal
pipeline into typed, native tool contracts:

  * ``bake_attribute_to_spatial_atlas`` — bake a point attribute over a frame
    range into a spatial flipbook atlas EXR, cooked directly from live geometry
    (the recommended way to produce a UE normal/height atlas — it bypasses the
    VAT vertex-index texture layout entirely).
  * ``reshape_to_spatial_atlas`` — reshape an already-baked vertex-index VAT EXR
    into a spatial flipbook atlas, purely in NumPy/OIIO (no re-cook).
  * ``get_vat_layout`` — introspect a Labs VAT ROP to report its texture layout
    and the vertex-index<->texel mapping so callers stop hand-computing it.
  * ``sample_vertex`` — read one vertex's VAT texel across frames and report
    whether the data is frozen or animated.

Each tool delegates to the matching handler running inside Houdini via the HTTP
bridge; the handlers use OpenImageIO + numpy (both ship in Houdini's Python).
"""

from __future__ import annotations

# Third-party
from mcp.server.fastmcp import Context

# Internal
from fxhoudinimcp.server import _get_bridge, mcp


@mcp.tool()
async def bake_attribute_to_spatial_atlas(
    ctx: Context,
    node_path: str,
    out_path: str,
    grid_cols: int,
    grid_rows: int,
    frames: list[float] | None = None,
    attrib_name: str = "N",
    index_mode: str = "ptnum",
    encode: str = "normal_halfunit",
    components: list[int] | None = None,
    tiles_x: int | None = None,
    tiles_y: int | None = None,
    pad: int = 1,
    flip_v: bool = False,
    axis_convert: bool = True,
    normal_x_sign: float = 1.0,
    normal_y_sign: float = 1.0,
    compression: str = "zip",
    timeout: float | None = None,
) -> dict:
    """Bake a point attribute from a live SOP into a spatial flipbook atlas EXR.

    Each frame is cooked directly from the Houdini session and written as one
    tile in a tiled atlas image.  The tile layout is *spatial*: point i at grid
    column/row maps to the matching pixel inside the tile, so the atlas can be
    sampled in UE by UV coordinates plus a flipbook frame index — without any
    vertex-index indirection.  This is the recommended path for normal atlases
    and height atlases destined for the UE VAT material.

    **Encode modes**

    * ``"normal_halfunit"`` (default) — interprets the attribute as a surface
      normal, optionally converts from Houdini Y-up to UE Z-up (see
      ``axis_convert``), normalises, and stores as ``v*0.5 + 0.5`` in the range
      [0, 1].  Produces a 3-channel atlas.
    * ``"scalar"`` — writes a single float component (component index given by
      ``components[0]``, default 0).  Produces a 1-channel atlas.  Useful for
      height (``attrib_name="P", components=[1]``) or mask attributes.
    * ``"raw"`` — writes the raw float components without remapping.
      ``components`` selects which tuple indices to include; when omitted all
      components are written.  Produces an N-channel atlas.

    **Tile layout** — by default a near-square tile grid is chosen
    automatically.  To produce an atlas a specific UE material expects, pass
    ``tiles_x`` / ``tiles_y`` explicitly (e.g. ``tiles_x=15, tiles_y=10`` for a
    150-frame atlas matched to the existing Custom-HLSL water material).

    A ``pad``-pixel edge-replicate gutter is added around each tile to prevent
    bilinear-filter bleed between consecutive flipbook frames.  With
    ``axis_convert=True`` the handler remaps Houdini space (X right, Y up, Z
    toward viewer) to UE local (X right, Z up, Y toward viewer) before encoding;
    ``normal_x_sign`` / ``normal_y_sign`` flip the X/Y axes if the import mirrors.

    Args:
        node_path: Houdini SOP node path (e.g. ``"/obj/water/OUT_water"``).
        out_path: Destination EXR path, as seen by the Houdini session. The
            parent directory is created automatically.
        grid_cols: Logical grid width — point columns (= inner tile pixel width).
        grid_rows: Logical grid height — point rows (= inner tile pixel height).
        frames: Frame spec. None → every integer frame in the playback range.
            ``[first, last]`` → inclusive range. ``[first, last, step]`` →
            stepped range. Explicit list of floats → used as-is.
        attrib_name: Point attribute to bake (default ``"N"`` for normals).
        index_mode: ``"ptnum"`` maps point i to col=i%grid_cols, row=i//grid_cols
            (requires npoints == grid_cols*grid_rows). ``"uv"`` reads the ``uv``
            point attribute and maps u→col, v→row (any topology).
        encode: ``"normal_halfunit"``, ``"scalar"``, or ``"raw"`` (see above).
        components: For ``"scalar"`` a one-int list (default ``[0]``); for
            ``"raw"`` the component indices to include (default all).
        tiles_x: Atlas column count. Omit for an automatic near-square layout.
        tiles_y: Atlas row count. Omit for an automatic layout.
        pad: Gutter width in pixels around each tile (default 1; 0 disables).
        flip_v: When True row 0 maps to the bottom of the tile (GL vs DX V axis).
        axis_convert: Convert Houdini Y-up → UE Z-up for normal encode
            (default True; ignored for non-normal encodes).
        normal_x_sign: Flip the UE-space X normal component (default 1.0).
        normal_y_sign: Flip the UE-space Y normal component (default 1.0).
        compression: EXR compression codec (default ``"zip"``).
        timeout: Bridge timeout in seconds. A full 150-frame, 160k-point bake
            can exceed the 120s default; pass e.g. 600.
    """
    bridge = _get_bridge(ctx)
    params: dict = {
        "node_path": node_path,
        "out_path": out_path,
        "grid_cols": grid_cols,
        "grid_rows": grid_rows,
        "attrib_name": attrib_name,
        "index_mode": index_mode,
        "encode": encode,
        "pad": pad,
        "flip_v": flip_v,
        "axis_convert": axis_convert,
        "normal_x_sign": normal_x_sign,
        "normal_y_sign": normal_y_sign,
        "compression": compression,
    }
    if frames is not None:
        params["frames"] = frames
    if components is not None:
        params["components"] = components
    if tiles_x is not None:
        params["tiles_x"] = tiles_x
    if tiles_y is not None:
        params["tiles_y"] = tiles_y
    return await bridge.execute(
        "vat.bake_attribute_to_spatial_atlas", params, timeout=timeout
    )


@mcp.tool()
async def reshape_to_spatial_atlas(
    ctx: Context,
    src_path: str,
    out_path: str,
    grid_cols: int,
    grid_rows: int,
    frames: int,
    vat_width: int | None = None,
    rows_per_frame: int | None = None,
    frame_start_row: int = 0,
    src_channels: list[int] | None = None,
    decode: str = "normal_halfunit",
    axis_convert: bool = True,
    normal_x_sign: float = 1.0,
    normal_y_sign: float = 1.0,
    flip_v: bool = False,
    tiles_x: int = 15,
    tiles_y: int = 10,
    pad: int = 1,
    compression: str = "zip",
    timeout: float | None = None,
) -> dict:
    """Reshape an existing vertex-index VAT EXR into a spatial UV flipbook atlas.

    Labs Vertex Animation Textures bakes geometry in vertex-index layout: each
    EXR row holds consecutive point data packed left-to-right across vat_width
    columns, with frames stacked vertically.  Real-time shaders prefer a spatial
    flipbook atlas where each tile holds one frame of data arranged so that
    UV0.xy maps directly to pixel (col, row) — pixel column = geometry grid X
    index, pixel row = geometry grid Z/Y index.  This reshape runs purely in
    NumPy/OIIO on the already-baked EXR, without re-cooking any geometry.

    Layout: tile ``(f % tiles_x, f // tiles_x)`` holds frame f.  Each tile is
    ``grid_cols x grid_rows`` pixels plus an edge-replicated gutter of ``pad``
    pixels on every side.

    decode modes:
    - ``"normal_halfunit"`` (default): source channels store normals encoded as
      ``v*0.5+0.5`` in Houdini space; the tool decodes them, then re-encodes in
      UE convention (Houdini Y-up → UE Z-up swap + optional sign flips).
    - ``"raw"``: clips to [0,1] and writes as-is (position / mask / etc.).

    Args:
        src_path: Source VAT EXR (vertex-index layout), as seen by Houdini.
        out_path: Output atlas EXR path (parent dir created automatically).
        grid_cols: Geometry grid columns (= tile inner pixel width).
        grid_rows: Geometry grid rows (= tile inner pixel height).
        frames: Number of frames to extract from the source EXR.
        vat_width: Expected source width; defaults to the actual EXR width and
            must match when provided.
        rows_per_frame: VAT rows consumed per frame; defaults to
            ceil(grid_cols*grid_rows / vat_width).
        frame_start_row: First source row belonging to frame 0 (default 0).
        src_channels: 0-based channel indices to read (default ``[0,1,2]``).
            Must have >= 3 entries for ``"normal_halfunit"`` decode.
        decode: ``"normal_halfunit"`` or ``"raw"``.
        axis_convert: Apply Houdini Y-up → UE Z-up for normal decode (default True).
        normal_x_sign: X-component sign for normal re-encode (default 1.0).
        normal_y_sign: Y-component sign for normal re-encode (default 1.0).
        flip_v: Invert the pixel-row index within each tile (default False).
        tiles_x: Tiles along the atlas X axis (default 15).
        tiles_y: Tiles along the atlas Y axis (default 10).
        pad: Edge-replicated gutter width in pixels (default 1; 0 disables).
        compression: EXR compression codec (default ``"zip"``).
        timeout: Bridge timeout in seconds. Increase for very large atlases.
    """
    bridge = _get_bridge(ctx)
    params: dict = {
        "src_path": src_path,
        "out_path": out_path,
        "grid_cols": grid_cols,
        "grid_rows": grid_rows,
        "frames": frames,
        "frame_start_row": frame_start_row,
        "decode": decode,
        "axis_convert": axis_convert,
        "normal_x_sign": normal_x_sign,
        "normal_y_sign": normal_y_sign,
        "flip_v": flip_v,
        "tiles_x": tiles_x,
        "tiles_y": tiles_y,
        "pad": pad,
        "compression": compression,
    }
    if vat_width is not None:
        params["vat_width"] = vat_width
    if rows_per_frame is not None:
        params["rows_per_frame"] = rows_per_frame
    if src_channels is not None:
        params["src_channels"] = src_channels
    return await bridge.execute(
        "vat.reshape_to_spatial_atlas", params, timeout=timeout
    )


@mcp.tool()
async def get_vat_layout(
    ctx: Context,
    node_path: str,
    num_points: int | None = None,
) -> dict:
    """Introspect a Labs VAT ROP to report its texture layout and the
    vertex-index-to-texel mapping formula so callers never hand-compute it.

    Walks all parameters on the node to build a full snapshot, then uses
    prioritized candidate name lists to discover: texture dimensions, bake
    mode (soft/rigid/fluid), frame range, FPS, compressed-normals-in-pos-alpha
    flag, spare-color flag, and all output file paths with role guesses and
    existence checks.

    When both texture width and num_points are known, the handler computes
    rows_per_frame and returns a structured ``texel_mapping`` dict containing
    the formula string plus the numeric fields (W, rows_per_frame,
    frame_start_row) needed to convert a vertex index + frame index to
    (vat_x, vat_y) texture coordinates.

    Every discovered value records the parm name chosen and the candidates
    tried, and the full ``all_parms`` snapshot is always returned, so the
    caller can cross-check against the live node.  Missing values are reported
    as None rather than raising.

    Args:
        node_path: Houdini path to the Labs Vertex Animation Textures 3.0 ROP
            (e.g. ``"/out/VAT_waterwave"``).
        num_points: Total source point count. When omitted the handler tries
            the node's first wired input or a ``soppath``/``sop`` parm; provide
            it explicitly when the SOP is not yet cooked.
    """
    bridge = _get_bridge(ctx)
    params: dict = {"node_path": node_path}
    if num_points is not None:
        params["num_points"] = num_points
    return await bridge.execute("vat.get_vat_layout", params)


@mcp.tool()
async def sample_vertex(
    ctx: Context,
    src_path: str,
    vertex_index: int | None = None,
    grid_col: int | None = None,
    grid_row: int | None = None,
    grid_cols: int | None = None,
    frames: list[int] | None = None,
    vat_width: int | None = None,
    rows_per_frame: int | None = None,
    frame_start_row: int = 0,
    channels: list[int] | None = None,
    decode: str = "raw",
    frozen_tol: float = 1e-6,
) -> dict:
    """Sample one vertex's VAT texel across frames; report frozen-vs-animated.

    Reads the VAT EXR directly and extracts the pixel at the texel column/row
    corresponding to the requested vertex for every requested frame row, then
    computes per-channel range statistics and decides whether the data is
    frozen (all frames agree within ``frozen_tol``) or animated.

    This is the primary tool for one-call confirmation of questions like
    "is the pos-alpha channel static while the col2 channel animates?",
    replacing the manual workflow of computing texel coordinates and eyeballing
    values across frames.

    Texel location for atlas width W: ``vat_x = vertex_index % W``,
    ``vat_row = vertex_index // W``; for frame f:
    ``vat_y = f * rows_per_frame + frame_start_row + vat_row``.

    Args:
        src_path: VAT EXR path, as seen by the Houdini session.
        vertex_index: 0-based flat vertex index. Provide this OR the
            ``grid_col`` / ``grid_row`` / ``grid_cols`` triplet.
        grid_col: Vertex column within a regular grid (0-based).
        grid_row: Vertex row within a regular grid (0-based).
        grid_cols: Grid column count; used with grid_col/grid_row to compute
            ``vertex_index = grid_row * grid_cols + grid_col``.
        frames: 0-based frame indices to sample. None infers the full range
            from image height and rows_per_frame. A two-element ``[start, end]``
            list is an inclusive range; a longer list is used as-is.
        vat_width: Expected atlas width; must match the image when provided.
        rows_per_frame: Image rows per frame band. Required to map frame → row
            (and to infer the frame count when frames=None).
        frame_start_row: First image row of frame 0 (default 0).
        channels: 0-based channel indices to extract (default all).
        decode: ``"raw"`` returns stored values; ``"normal_halfunit"`` also
            decodes via ``v*2-1`` and adds a ``decoded`` key per frame.
        frozen_tol: Max per-channel range (max − min) still counted as frozen
            (default 1e-6).
    """
    bridge = _get_bridge(ctx)
    params: dict = {
        "src_path": src_path,
        "frame_start_row": frame_start_row,
        "decode": decode,
        "frozen_tol": frozen_tol,
    }
    if vertex_index is not None:
        params["vertex_index"] = vertex_index
    if grid_col is not None:
        params["grid_col"] = grid_col
    if grid_row is not None:
        params["grid_row"] = grid_row
    if grid_cols is not None:
        params["grid_cols"] = grid_cols
    if frames is not None:
        params["frames"] = frames
    if vat_width is not None:
        params["vat_width"] = vat_width
    if rows_per_frame is not None:
        params["rows_per_frame"] = rows_per_frame
    if channels is not None:
        params["channels"] = channels
    return await bridge.execute("vat.sample_vertex", params)
