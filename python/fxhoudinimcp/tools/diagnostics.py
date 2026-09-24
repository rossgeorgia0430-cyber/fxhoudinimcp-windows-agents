"""MCP tools for read-only diagnostics: geometry chains and diffs, mesh topology and UVs,
ray casts, name-keyed motion and RBD statistics, and FBX / HIP file inspection."""

from __future__ import annotations

# Built-in
from typing import Any

# Third-party
from mcp.server.fastmcp import Context

# Internal
from fxhoudinimcp.server import _get_bridge, mcp


@mcp.tool()
async def trace_chain_counts(
    ctx: Context,
    node_path: str,
    depth: int = 20,
    input_index: int = 0,
) -> dict:
    """Walk upstream from a SOP node and report per-node point/prim/vertex counts plus deltas — pinpoints where geometry collapses or expands along a chain.

    Args:
        node_path: SOP node to trace from.
        depth: Max upstream nodes to walk (default 20).
        input_index: Which input to follow upstream (default 0).
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "diagnostics.trace_chain_counts",
        {
            "node_path": node_path,
            "depth": depth,
            "input_index": input_index,
        },
    )


@mcp.tool()
async def attribute_profile(
    ctx: Context,
    node_path: str,
    bin_attrib: str,
    bins: int = 10,
    value_attrib: str | None = None,
    bin_min: float | None = None,
    bin_max: float | None = None,
    attrib_class: str = "point",
) -> dict:
    """Bin elements by a scalar attribute into equal-width bins and report per-bin count and optional mean of a second attribute — a density / along-axis distribution profile.

    Args:
        node_path: SOP node path.
        bin_attrib: Scalar attribute to bin by, e.g. an arclength attribute.
        bins: Number of bins (default 10).
        value_attrib: Optional attribute to average per bin, e.g. "pscale".
        bin_min: Optional explicit range minimum; default is the attribute's
            observed minimum.
        bin_max: Optional explicit range maximum; default is the attribute's
            observed maximum.
        attrib_class: "point", "prim", or "vertex".
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {
        "node_path": node_path,
        "bin_attrib": bin_attrib,
        "bins": bins,
        "attrib_class": attrib_class,
    }
    if value_attrib is not None:
        params["value_attrib"] = value_attrib
    if bin_min is not None:
        params["bin_min"] = bin_min
    if bin_max is not None:
        params["bin_max"] = bin_max
    return await bridge.execute("diagnostics.attribute_profile", params)


@mcp.tool()
async def compare_points(
    ctx: Context,
    node_path_a: str,
    node_path_b: str,
    attrib_name: str = "P",
    attrib_class: str = "point",
    tolerance: float = 1e-6,
) -> dict:
    """Element-wise diff of an attribute between two SOP nodes — reports max absolute difference and how many elements exceed a tolerance. The canonical 'is this operation non-destructive / identity?' check.

    Args:
        node_path_a: First SOP node to compare.
        node_path_b: Second SOP node to compare.
        attrib_name: Attribute name (default "P").
        attrib_class: "point", "prim", or "vertex".
        tolerance: Max abs diff still treated as identical (default 1e-6).
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "diagnostics.compare_points",
        {
            "node_path_a": node_path_a,
            "node_path_b": node_path_b,
            "attrib_name": attrib_name,
            "attrib_class": attrib_class,
            "tolerance": tolerance,
        },
    )


@mcp.tool()
async def analyze_alembic_output(
    ctx: Context,
    node_path: str,
    output_sop_path: str | None = None,
    start_frame: float | None = None,
    end_frame: float | None = None,
    frame_step: float | None = None,
    sample_count: int | None = 8,
    fps: float | None = None,
) -> dict:
    """Profile an Alembic output SOP/ROP across representative frames.

    Reports frame range/FPS, sampled cook time, geometry counts, bounding
    boxes, attributes, count-observed topology changes, and estimated total
    geometry-cook duration. The estimate excludes Alembic encoding/disk I/O.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"node_path": node_path}
    for key, value in (
        ("output_sop_path", output_sop_path),
        ("start_frame", start_frame),
        ("end_frame", end_frame),
        ("frame_step", frame_step),
        ("sample_count", sample_count),
        ("fps", fps),
    ):
        if value is not None:
            params[key] = value
    return await bridge.execute("diagnostics.analyze_alembic_output", params)


@mcp.tool()
async def check_houdini_to_ue_space(
    ctx: Context,
    input_node_path: str,
    output_node_path: str,
    scale: float = 100.0,
    tolerance: float = 1e-4,
    max_point_samples: int = 2048,
) -> dict:
    """Verify ``UE=(H.x,-H.y,H.z)*scale`` between two ordered SOP meshes.

    Checks the transformed bounding box and, when point counts/order match,
    evenly sampled positions. It directly catches a missing Y handedness flip.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "diagnostics.check_houdini_to_ue_space",
        {
            "input_node_path": input_node_path,
            "output_node_path": output_node_path,
            "scale": scale,
            "tolerance": tolerance,
            "max_point_samples": max_point_samples,
        },
    )


@mcp.tool()
async def mesh_topology_report(
    ctx: Context,
    node_path: str,
    piece_attrib: str | None = None,
    max_pieces: int = 20,
) -> dict:
    """Prove whether a mesh is closed and connected before fracturing, booleans, or export.

    Reports closed polygons, open curves (open polylines), non-polygon prims,
    connected components, boundary (open) and non-manifold edges, surface area,
    and for closed shells the enclosed volume and whether normals face outward.
    Faces connect only through shared points, so unfused UV-seam points show up
    as open edges. With ``piece_attrib`` (e.g. "name") every piece is measured
    separately: pieces made of several parts, open pieces, or pieces carrying
    open curves are listed, plus the closed-volume distribution.

    Args:
        node_path: SOP node path.
        piece_attrib: Optional primitive attribute that identifies pieces.
        max_pieces: Max problem pieces listed.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"node_path": node_path, "max_pieces": max_pieces}
    if piece_attrib is not None:
        params["piece_attrib"] = piece_attrib
    return await bridge.execute("diagnostics.mesh_topology_report", params)


@mcp.tool()
async def uv_quality_report(
    ctx: Context,
    node_path: str,
    uv_attrib: str = "uv",
    groups: list[str] | None = None,
) -> dict:
    """Check UVs per primitive group: collapsed, flipped, and texel-scale spread.

    For every closed polygon compares UV area against 3D area. Reports
    polygons whose UVs collapsed to a line or point (degenerate_uv), polygons
    wound against the majority (flipped_uv; mirrored islands count), texel scale (UV units per scene
    unit) p1/p50/p99 and their spread, and the UV bounds. Use it on fracture
    interiors or any generated UVs before export.

    Args:
        node_path: SOP node path.
        uv_attrib: Vertex or point UV attribute (default "uv").
        groups: Primitive group patterns to report separately, e.g.
            ["inside", "* ^inside"]. Omitted → all primitives.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"node_path": node_path, "uv_attrib": uv_attrib}
    if groups is not None:
        params["groups"] = groups
    return await bridge.execute("diagnostics.uv_quality_report", params)


@mcp.tool()
async def ray_intersect(
    ctx: Context,
    node_path: str,
    direction: list[float] | None = None,
    origins: list[list[float]] | None = None,
    grid_center: list[float] | None = None,
    grid_size: float | None = None,
    grid_resolution: int = 10,
    max_distance: float | None = None,
    label_attrib: str | None = None,
    max_results: int = 200,
) -> dict:
    """Cast rays at SOP geometry: ground heights, clearances, and coverage holes.

    Pass explicit ``origins``, or ``grid_center`` + ``grid_size`` to cast a
    grid_resolution x grid_resolution grid of rays from a square facing
    ``direction`` (the way to find where a collision floor has gaps). Each ray
    reports hit position, normal, distance, and primitive; ``label_attrib``
    adds the hit primitive's attribute value (e.g. "name") and per-label hit
    counts. Positions are in the SOP's own space.

    Args:
        node_path: SOP node path.
        direction: Ray direction (default straight down, [0, -1, 0]).
        origins: Ray origins as [[x, y, z], ...].
        grid_center: Centre of the origin grid.
        grid_size: Edge length of the square origin grid.
        grid_resolution: Rays per grid edge.
        max_distance: Ignore hits farther than this.
        label_attrib: Primitive attribute reported for each hit.
        max_results: Max rays and miss origins listed.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {
        "node_path": node_path,
        "grid_resolution": grid_resolution,
        "max_results": max_results,
    }
    for key, value in (
        ("direction", direction),
        ("origins", origins),
        ("grid_center", grid_center),
        ("grid_size", grid_size),
        ("max_distance", max_distance),
        ("label_attrib", label_attrib),
    ):
        if value is not None:
            params[key] = value
    return await bridge.execute("diagnostics.ray_intersect", params)


@mcp.tool()
async def track_named_elements(
    ctx: Context,
    node_path: str,
    frames: list[float],
    names: list[str] | None = None,
    name_attrib: str = "name",
    attribs: list[str] | None = None,
    element_class: str = "point",
    include_bounds: bool = False,
    max_names: int = 50,
    timeout: float | None = None,
) -> dict:
    """Follow named points or primitives across frames, matched by name, not index.

    For each frame reads numeric attributes of every element with a given name
    (KineFX joints, packed RBD pieces, sim points) and optionally the bounds of
    all elements sharing the name (e.g. the prims of one unpacked piece). Use
    it to compare motion against a reference, derive velocities, or record
    per-piece centres before an export round-trip. Frames cook in the order
    given, so pass them ascending for simulations.

    Args:
        node_path: SOP node path.
        frames: Frames to sample.
        names: Names to follow. Omitted → the first max_names names, sorted.
        name_attrib: String or integer attribute holding the name.
        attribs: Numeric attributes to read (default ["P"]).
        element_class: "point" or "prim".
        include_bounds: Add min/max/center of all elements with the name.
        max_names: Cap when names is omitted.
        timeout: Bridge timeout in seconds for long frame lists.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {
        "node_path": node_path,
        "frames": frames,
        "name_attrib": name_attrib,
        "element_class": element_class,
        "include_bounds": include_bounds,
        "max_names": max_names,
    }
    if names is not None:
        params["names"] = names
    if attribs is not None:
        params["attribs"] = attribs
    return await bridge.execute("diagnostics.track_named_elements", params, timeout=timeout)


@mcp.tool()
async def rbd_motion_stats(
    ctx: Context,
    node_path: str,
    start_frame: float,
    end_frame: float,
    sample_frames: list[float] | None = None,
    name_attrib: str = "name",
    center: list[float] | None = None,
    up_axis: str = "y",
    moving_speed: float = 0.3,
    rebound_speed: float = 0.5,
    reset_node_path: str | None = None,
    top_n: int = 5,
    timeout: float | None = None,
) -> dict:
    """Step a rigid-body result frame by frame and measure how it actually moves.

    Reads named points (e.g. the RBD Bullet Solver's simulation points, or any
    packed-piece output) every frame from start to end and reports, at sample
    frames: speed p50/max, moving count, horizontal spread from a centre
    (p50/p90/max), height min/max, and max spin (``w``). Over the whole range:
    the settle frame, the lowest point reached (floor penetration), per-piece
    peak upward speed after start (rebound strength), and the farthest pieces.
    Velocity comes from ``v`` when present, otherwise from P differences. These
    are the numbers to compare when tuning bounce, drag, gravity, or impulses.

    Args:
        node_path: SOP node whose points are the pieces.
        start_frame: First frame (the solver start or handoff frame).
        end_frame: Last frame.
        sample_frames: Frames reported in detail. Omitted → 8 spread frames.
        name_attrib: Point attribute naming each piece.
        center: Spread origin. Omitted → centroid at start_frame.
        up_axis: "x", "y", or "z".
        moving_speed: Speed above which a piece counts as moving.
        rebound_speed: Upward speed above which a piece counts as rebounding.
        reset_node_path: Solver SOP or DOP network to reset first, so the whole
            range re-simulates and the timing covers it.
        top_n: Farthest pieces listed.
        timeout: Bridge timeout in seconds for long simulations.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {
        "node_path": node_path,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "name_attrib": name_attrib,
        "up_axis": up_axis,
        "moving_speed": moving_speed,
        "rebound_speed": rebound_speed,
        "top_n": top_n,
    }
    for key, value in (
        ("sample_frames", sample_frames),
        ("center", center),
        ("reset_node_path", reset_node_path),
    ):
        if value is not None:
            params[key] = value
    return await bridge.execute("diagnostics.rbd_motion_stats", params, timeout=timeout)


@mcp.tool()
async def inspect_fbx(
    ctx: Context,
    file_path: str,
    compare_sop_path: str | None = None,
    name_attrib: str = "name",
    frames: list[float] | None = None,
    max_listed: int = 20,
    timeout: float | None = None,
) -> dict:
    """Re-import an exported FBX and prove what it contains.

    Imports into a temporary subnet (deleted afterwards; units converted to
    meters) and reports object types, mesh nodes, closed polygons, other
    primitives (curves), the transform key range, material names, mesh nodes
    that stay static while the file is animated, and curve-only nodes (open
    polylines the FBX ROP split into their own node, which then take the
    animation from the mesh). With ``compare_sop_path`` pointing at
    the unpacked source pieces, each mesh node's world bbox centre is compared
    with the same-named piece (non [A-Za-z0-9_] characters become "_", as the
    importer does) at ``frames`` (default: first, middle, and last key).

    Args:
        file_path: FBX path.
        compare_sop_path: Unpacked SOP with one name per piece.
        name_attrib: Primitive attribute naming the pieces.
        frames: Comparison frames.
        max_listed: Cap for listed names.
        timeout: Bridge timeout in seconds for large files.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {
        "file_path": file_path,
        "name_attrib": name_attrib,
        "max_listed": max_listed,
    }
    if compare_sop_path is not None:
        params["compare_sop_path"] = compare_sop_path
    if frames is not None:
        params["frames"] = frames
    return await bridge.execute("diagnostics.inspect_fbx", params, timeout=timeout)


@mcp.tool()
async def inspect_hip_file(
    ctx: Context,
    hip_path: str,
    root_path: str = "/obj",
    max_depth: int = 2,
    max_nodes: int = 100,
    timeout: float | None = None,
) -> dict:
    """Read another HIP file's network without opening it in the live session.

    A separate hython loads the file and lists, below ``root_path``, every
    node's type, inputs, flags, and only its non-default parameters (values,
    expressions, key counts; long strings such as VEX snippets are kept up to
    4000 characters). Locked asset internals are skipped except editable
    sections, e.g. a solver's forces subnet. Narrow ``root_path`` to the node
    of interest and raise ``max_depth`` to see inside it.

    Args:
        hip_path: HIP file to read.
        root_path: Node to start from.
        max_depth: Levels below root_path.
        max_nodes: Max nodes returned.
        timeout: Bridge timeout in seconds; loading a large HIP takes a while.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "diagnostics.inspect_hip_file",
        {
            "hip_path": hip_path,
            "root_path": root_path,
            "max_depth": max_depth,
            "max_nodes": max_nodes,
        },
        timeout=timeout,
    )
