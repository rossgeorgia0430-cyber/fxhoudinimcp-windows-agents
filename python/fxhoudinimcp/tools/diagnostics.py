"""MCP tools for read-only geometry diagnostics (chain counts, attribute profiles, node-vs-node diffs)."""

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
