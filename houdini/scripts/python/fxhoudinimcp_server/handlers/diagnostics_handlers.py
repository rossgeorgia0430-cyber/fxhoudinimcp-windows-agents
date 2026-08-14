"""Diagnostic (read-only) handlers for FXHoudini-MCP.

Each handler reads geometry on SOP nodes to help diagnose graph problems
(point-count collapses, distribution/density issues, non-identity ops) —
none of them ever modify the scene.
All functions run on the main thread via the dispatcher.
"""

from __future__ import annotations

# Built-in
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_handler

###### Helpers

def _get_sop_geo(node_path: str) -> hou.Geometry:
    """Return the cooked read-only geometry for a SOP node.

    Raises:
        hou.OperationFailed: if the node doesn't exist or has no geometry.
    """
    node = hou.node(node_path)
    if node is None:
        raise hou.OperationFailed(f"Node not found: {node_path}")
    geo = node.geometry()
    if geo is None:
        raise hou.OperationFailed(f"Node has no geometry: {node_path}")
    return geo


def _find_attrib_by_class(geo: hou.Geometry, attrib_name: str, attrib_class: str) -> hou.Attrib:
    """Find an attribute for the given class ("point"/"prim"/"vertex").

    Raises:
        hou.OperationFailed: if attrib_class is not one of point/prim/vertex,
            or the attribute doesn't exist on that class.
    """
    cls = attrib_class.lower()
    finders = {
        "point": geo.findPointAttrib,
        "prim": geo.findPrimAttrib,
        "vertex": geo.findVertexAttrib,
    }
    finder = finders.get(cls)
    if finder is None:
        raise hou.OperationFailed(
            f"Invalid attrib_class: {attrib_class!r}. Use one of "
            f"{list(finders.keys())}")
    attrib = finder(attrib_name)
    if attrib is None:
        raise hou.OperationFailed(f"attribute not found: {attrib_name}")
    return attrib


def _read_flat_values_by_class(
    geo: hou.Geometry, attrib_name: str, attrib_class: str
) -> tuple[list[float], int]:
    """Read an attribute's flat, element-major float values for a class.

    Returns:
        A ``(flat_values, tuple_size)`` pair. ``flat_values`` has
        ``count * tuple_size`` entries (every ``tuple_size`` consecutive
        values is one element).

    Raises:
        hou.OperationFailed: if attrib_class is invalid or the attribute is
            missing.
    """
    attrib = _find_attrib_by_class(geo, attrib_name, attrib_class)
    cls = attrib_class.lower()
    values_getters = {
        "point": geo.pointFloatAttribValues,
        "prim": geo.primFloatAttribValues,
        "vertex": geo.vertexFloatAttribValues,
    }
    flat = list(values_getters[cls](attrib_name))
    return flat, attrib.size()


###### diagnostics.trace_chain_counts

def _trace_chain_counts(
    *,
    node_path: str,
    depth: int = 20,
    input_index: int = 0,
) -> dict[str, Any]:
    """Walk upstream from node_path and report per-node geometry counts.

    Follows input ``input_index`` upstream, up to ``depth`` nodes, so a
    caller can spot where point count collapses or expands along a chain.
    One bad/uncookable node along the way does not abort the trace — its
    entry carries an "error" field instead.
    """
    start = hou.node(node_path)
    if start is None:
        raise hou.OperationFailed(f"Node not found: {node_path}")

    chain: list[hou.Node] = [start]
    seen: set[str] = {start.path()}
    cur = start
    while len(chain) < depth + 1:
        ins = cur.inputs()
        nxt = ins[input_index] if (ins and len(ins) > input_index) else None
        if nxt is None or nxt.path() in seen:
            break
        seen.add(nxt.path())
        chain.append(nxt)
        cur = nxt

    # chain is currently node_path -> upstream; reverse to upstream -> node_path.
    chain.reverse()

    entries: list[dict[str, Any]] = []
    previous_points: int | None = None
    for n in chain:
        entry: dict[str, Any] = {
            "name": n.name(),
            "path": n.path(),
            "type": n.type().name(),
            "points": None,
            "prims": None,
            "vertices": None,
            "delta_points": None,
        }
        try:
            geo = n.geometry()
            if geo is not None:
                points = int(geo.intrinsicValue("pointcount"))
                prims = int(geo.intrinsicValue("primitivecount"))
                vertices = int(geo.intrinsicValue("vertexcount"))
                entry["points"] = points
                entry["prims"] = prims
                entry["vertices"] = vertices
                if previous_points is not None:
                    entry["delta_points"] = points - previous_points
                previous_points = points
            else:
                previous_points = None
        except Exception as exc:  # noqa: BLE001 - one bad node must not kill the trace
            entry["error"] = str(exc)
            previous_points = None
        entries.append(entry)

    collapses: list[dict[str, Any]] = []
    for prev_entry, entry in zip(entries, entries[1:], strict=False):
        delta = entry["delta_points"]
        if delta is not None and delta < 0:
            collapses.append({
                "from": prev_entry["name"],
                "to": entry["name"],
                "delta_points": delta,
            })

    return {
        "node_path": node_path,
        "input_index": input_index,
        "depth": depth,
        "chain": entries,
        "collapses": collapses,
    }

register_handler("diagnostics.trace_chain_counts", _trace_chain_counts)


###### diagnostics.attribute_profile

def _attribute_profile(
    *,
    node_path: str,
    bin_attrib: str,
    bins: int = 10,
    value_attrib: str | None = None,
    bin_min: float | None = None,
    bin_max: float | None = None,
    attrib_class: str = "point",
) -> dict[str, Any]:
    """Bin elements by a scalar attribute and report per-bin count/mean.

    Useful for density/distribution checks along an axis, e.g. binning
    points by arclength ("dist_behind") and averaging "pscale" per bin.
    """
    geo = _get_sop_geo(node_path)

    cls = attrib_class.lower()
    if cls not in ("point", "prim", "vertex"):
        raise hou.OperationFailed(
            "attribute_profile requires point/prim/vertex class")

    ba = _find_attrib_by_class(geo, bin_attrib, cls)
    if ba.size() != 1:
        raise hou.OperationFailed("bin_attrib must be scalar (size 1)")

    bvals, _ = _read_flat_values_by_class(geo, bin_attrib, cls)
    n = len(bvals)
    if n == 0:
        return {
            "node_path": node_path,
            "bin_attrib": bin_attrib,
            "count": 0,
            "bins": [],
        }

    attrib_min = min(bvals)
    attrib_max = max(bvals)
    lo = bin_min if bin_min is not None else attrib_min
    hi = bin_max if bin_max is not None else attrib_max
    if hi <= lo:
        hi = lo + 1e-6

    num_bins = max(bins, 1)
    width = (hi - lo) / num_bins

    counts = [0] * num_bins
    sums = [0.0] * num_bins

    vflat: list[float] = []
    vsize = 1
    if value_attrib is not None:
        va = _find_attrib_by_class(geo, value_attrib, cls)
        vsize = va.size()
        vflat, _ = _read_flat_values_by_class(geo, value_attrib, cls)

    for i in range(n):
        idx = int((bvals[i] - lo) / width)
        if idx < 0:
            idx = 0
        elif idx > num_bins - 1:
            idx = num_bins - 1
        counts[idx] += 1
        if value_attrib is not None:
            if vsize == 1:
                value = vflat[i]
            else:
                base = i * vsize
                value = sum(
                    vflat[base + c] ** 2 for c in range(vsize)
                ) ** 0.5
            sums[idx] += value

    bin_rows: list[dict[str, Any]] = []
    for b in range(num_bins):
        cnt = counts[b]
        mean_value = (sums[b] / cnt) if (value_attrib is not None and cnt) else None
        bin_rows.append({
            "index": b,
            "lo": lo + b * width,
            "hi": lo + (b + 1) * width,
            "count": cnt,
            "mean_value": mean_value,
        })

    return {
        "node_path": node_path,
        "attrib_class": attrib_class,
        "bin_attrib": bin_attrib,
        "value_attrib": value_attrib,
        "count": n,
        "attrib_min": attrib_min,
        "attrib_max": attrib_max,
        "range_lo": lo,
        "range_hi": hi,
        "bins": bin_rows,
    }

register_handler("diagnostics.attribute_profile", _attribute_profile)


###### diagnostics.compare_points

def _compare_points(
    *,
    node_path_a: str,
    node_path_b: str,
    attrib_name: str = "P",
    attrib_class: str = "point",
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Element-wise diff of an attribute between two SOP nodes.

    The identity/regression check ("is this op non-destructive?"). Reports
    the max absolute difference and how many flat components exceed
    ``tolerance``.
    """
    geoA = _get_sop_geo(node_path_a)
    geoB = _get_sop_geo(node_path_b)

    cls = attrib_class.lower()
    if cls not in ("point", "prim", "vertex"):
        raise hou.OperationFailed(
            "compare_points requires point/prim/vertex class")

    attribA = _find_attrib_by_class(geoA, attrib_name, cls)
    if attribA is None:
        raise hou.OperationFailed(
            f"attribute not found: {attrib_name} on {node_path_a}")
    attribB = _find_attrib_by_class(geoB, attrib_name, cls)
    if attribB is None:
        raise hou.OperationFailed(
            f"attribute not found: {attrib_name} on {node_path_b}")

    flatA, sizeA = _read_flat_values_by_class(geoA, attrib_name, cls)
    flatB, sizeB = _read_flat_values_by_class(geoB, attrib_name, cls)

    countA = len(flatA) // sizeA if sizeA else 0
    countB = len(flatB) // sizeB if sizeB else 0

    if sizeA != sizeB or countA != countB:
        return {
            "identical": False,
            "reason": "count/size mismatch",
            "count_a": countA,
            "count_b": countB,
            "size_a": sizeA,
            "size_b": sizeB,
            "attrib_name": attrib_name,
        }

    max_abs_diff = 0.0
    num_exceeding = 0
    for a, b in zip(flatA, flatB, strict=True):
        diff = abs(a - b)
        if diff > max_abs_diff:
            max_abs_diff = diff
        if diff > tolerance:
            num_exceeding += 1

    return {
        "node_path_a": node_path_a,
        "node_path_b": node_path_b,
        "attrib_name": attrib_name,
        "attrib_class": attrib_class,
        "tolerance": tolerance,
        "count_a": countA,
        "count_b": countB,
        "size": sizeA,
        "max_abs_diff": max_abs_diff,
        "num_exceeding": num_exceeding,
        "identical": bool(max_abs_diff <= tolerance),
    }

register_handler("diagnostics.compare_points", _compare_points)
