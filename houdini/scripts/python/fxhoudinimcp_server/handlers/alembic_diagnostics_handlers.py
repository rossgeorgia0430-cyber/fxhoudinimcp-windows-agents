"""Read-only diagnostics for Alembic geometry output and Houdini-to-UE space.

This module deliberately owns no scene mutation.  Frame-changing diagnostics
restore the caller's frame before returning, including when cooking fails.
"""

from __future__ import annotations

# Built-in
import math
import time
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_handler


def _node_or_raise(node_path: str) -> hou.Node:
    node = hou.node(node_path)
    if node is None:
        raise hou.OperationFailed(f"Node not found: {node_path}")
    return node


def _geometry_node(node: hou.Node, output_sop_path: str | None) -> hou.Node:
    """Resolve a SOP from either a SOP path or an output ROP plus an override."""
    if output_sop_path:
        return _node_or_raise(output_sop_path)

    try:
        if node.geometry() is not None:
            return node
    except (AttributeError, hou.OperationFailed):
        pass

    # Common SOP/output path parameter names used by geometry and Alembic ROPs.
    for parm_name in ("soppath", "sop_path", "sopoutput", "objectpath1", "root"):
        parm = node.parm(parm_name)
        if parm is None:
            continue
        path = parm.evalAsString().strip()
        if not path:
            continue
        candidate = hou.node(path)
        if candidate is None:
            continue
        try:
            if candidate.geometry() is not None:
                return candidate
        except (AttributeError, hou.OperationFailed):
            display = getattr(candidate, "displayNode", lambda: None)()
            if display is not None:
                return display

    inputs = node.inputs()
    if inputs:
        candidate = inputs[0]
        try:
            if candidate is not None and candidate.geometry() is not None:
                return candidate
        except (AttributeError, hou.OperationFailed):
            pass

    raise hou.OperationFailed(
        f"Could not resolve output SOP from {node.path()}; pass output_sop_path explicitly"
    )


def _frame_settings(
    node: hou.Node,
    start_frame: float | None,
    end_frame: float | None,
    frame_step: float | None,
) -> tuple[float, float, float, str]:
    if start_frame is not None or end_frame is not None:
        if start_frame is None or end_frame is None:
            raise hou.OperationFailed("start_frame and end_frame must be provided together")
        step = 1.0 if frame_step is None else float(frame_step)
        source = "arguments"
    else:
        f1 = node.parm("f1")
        f2 = node.parm("f2")
        f3 = node.parm("f3")
        trange = node.parm("trange")
        use_rop_range = f1 is not None and f2 is not None and (
            trange is None or int(trange.eval()) != 0
        )
        if use_rop_range:
            start_frame = float(f1.eval())
            end_frame = float(f2.eval())
            step = float(f3.eval()) if f3 is not None else 1.0
            source = "rop"
        else:
            start_frame, end_frame = (float(v) for v in hou.playbar.frameRange())
            step = 1.0
            source = "playbar"
        if frame_step is not None:
            step = float(frame_step)
    if step <= 0:
        raise hou.OperationFailed("frame_step must be greater than zero")
    if float(end_frame) < float(start_frame):
        raise hou.OperationFailed("end_frame must be greater than or equal to start_frame")
    return float(start_frame), float(end_frame), step, source


def _all_frames(start: float, end: float, step: float) -> list[float]:
    count = int(math.floor((end - start) / step + 1e-9)) + 1
    return [start + i * step for i in range(count)]


def _sample_frames(frames: list[float], sample_count: int | None) -> list[float]:
    if sample_count is None or sample_count <= 0 or sample_count >= len(frames):
        return frames
    if sample_count == 1:
        return [frames[len(frames) // 2]]
    indices = {
        int(round(i * (len(frames) - 1) / (sample_count - 1)))
        for i in range(sample_count)
    }
    return [frames[i] for i in sorted(indices)]


def _attrib_names(geo: hou.Geometry) -> dict[str, list[str]]:
    return {
        "point": sorted(a.name() for a in geo.pointAttribs()),
        "vertex": sorted(a.name() for a in geo.vertexAttribs()),
        "prim": sorted(a.name() for a in geo.primAttribs()),
    }


def _bbox(geo: hou.Geometry) -> dict[str, list[float]]:
    bbox = geo.boundingBox()
    return {
        "min": list(bbox.minvec()),
        "max": list(bbox.maxvec()),
        "size": list(bbox.sizevec()),
        "center": list(bbox.center()),
    }


def _topology_counts(geo: hou.Geometry) -> tuple[int, int, int]:
    return (
        int(geo.intrinsicValue("pointcount")),
        int(geo.intrinsicValue("primitivecount")),
        int(geo.intrinsicValue("vertexcount")),
    )


def analyze_alembic_output(
    *,
    node_path: str,
    output_sop_path: str | None = None,
    start_frame: float | None = None,
    end_frame: float | None = None,
    frame_step: float | None = None,
    sample_count: int | None = 8,
    fps: float | None = None,
) -> dict[str, Any]:
    """Profile an Alembic output SOP/ROP over all or sampled frames.

    Topology variation is conservatively detected from point/primitive/vertex
    counts.  ``changing_topology=False`` therefore means "not observed from
    counts", not proof that connectivity is identical.
    """
    source = _node_or_raise(node_path)
    sop = _geometry_node(source, output_sop_path)
    start, end, step, range_source = _frame_settings(
        source, start_frame, end_frame, frame_step
    )
    frames = _all_frames(start, end, step)
    sampled = _sample_frames(frames, sample_count)
    effective_fps = float(hou.fps() if fps is None else fps)
    if effective_fps <= 0:
        raise hou.OperationFailed("fps must be greater than zero")

    original_frame = float(hou.frame())
    rows: list[dict[str, Any]] = []
    try:
        for frame in sampled:
            hou.setFrame(frame)
            started = time.perf_counter()
            geo = sop.geometry()
            elapsed = time.perf_counter() - started
            if geo is None:
                raise hou.OperationFailed(f"Node has no geometry: {sop.path()}")
            points, prims, vertices = _topology_counts(geo)
            rows.append({
                "frame": frame,
                "cook_seconds": elapsed,
                "points": points,
                "primitives": prims,
                "vertices": vertices,
                "bbox": _bbox(geo),
                "attributes": _attrib_names(geo),
            })
    finally:
        hou.setFrame(original_frame)

    topology_signatures = {
        (row["points"], row["primitives"], row["vertices"]) for row in rows
    }
    mean_cook = sum(row["cook_seconds"] for row in rows) / len(rows) if rows else 0.0
    total_frames = len(frames)
    return {
        "node_path": node_path,
        "output_sop_path": sop.path(),
        "frame_range": {
            "start": start,
            "end": end,
            "step": step,
            "frame_count": total_frames,
            "source": range_source,
        },
        "fps": effective_fps,
        "duration_seconds": total_frames / effective_fps,
        "sampled_frames": sampled,
        "samples": rows,
        "mean_cook_seconds": mean_cook,
        "estimated_total_cook_seconds": mean_cook * total_frames,
        "changing_topology": len(topology_signatures) > 1,
        "topology_check": "point_primitive_vertex_counts",
        "topology_check_note": (
            "False means no count change was observed; connectivity-only changes are not detected"
        ),
    }


def _expected_bbox(input_bbox: dict[str, list[float]], scale: float) -> dict[str, list[float]]:
    in_min = input_bbox["min"]
    in_max = input_bbox["max"]
    x_values = (in_min[0] * scale, in_max[0] * scale)
    y_values = (-in_min[1] * scale, -in_max[1] * scale)
    z_values = (in_min[2] * scale, in_max[2] * scale)
    out_min = [min(x_values), min(y_values), min(z_values)]
    out_max = [max(x_values), max(y_values), max(z_values)]
    return {
        "min": out_min,
        "max": out_max,
        "size": [out_max[i] - out_min[i] for i in range(3)],
        "center": [(out_max[i] + out_min[i]) * 0.5 for i in range(3)],
    }


def _bbox_matches(a: dict[str, list[float]], b: dict[str, list[float]], tolerance: float) -> bool:
    return all(
        abs(a[key][axis] - b[key][axis]) <= tolerance
        for key in ("min", "max")
        for axis in range(3)
    )


def check_houdini_to_ue_space(
    *,
    input_node_path: str,
    output_node_path: str,
    scale: float = 100.0,
    tolerance: float = 1e-4,
    max_point_samples: int = 2048,
) -> dict[str, Any]:
    """Check ``UE=(H.x, -H.y, H.z)*scale`` between two SOP geometries."""
    if scale == 0:
        raise hou.OperationFailed("scale must be non-zero")
    if tolerance < 0:
        raise hou.OperationFailed("tolerance must be non-negative")
    input_geo = _geometry_node(_node_or_raise(input_node_path), None).geometry()
    output_geo = _geometry_node(_node_or_raise(output_node_path), None).geometry()
    if input_geo is None or output_geo is None:
        raise hou.OperationFailed("Both nodes must provide cooked geometry")

    input_bbox = _bbox(input_geo)
    output_bbox = _bbox(output_geo)
    expected_bbox = _expected_bbox(input_bbox, scale)
    bbox_matches = _bbox_matches(output_bbox, expected_bbox, tolerance)

    input_count = int(input_geo.intrinsicValue("pointcount"))
    output_count = int(output_geo.intrinsicValue("pointcount"))
    point_check: dict[str, Any] = {
        "performed": False,
        "reason": "point counts differ",
        "input_points": input_count,
        "output_points": output_count,
    }
    point_matches: bool | None = None
    if input_count == output_count:
        sample_limit = max(1, int(max_point_samples))
        sample_count = min(input_count, sample_limit)
        indices = _sample_frames(list(range(input_count)), sample_count)
        max_error = 0.0
        failures = 0
        for index in indices:
            source_pos = input_geo.point(int(index)).position()
            actual = output_geo.point(int(index)).position()
            expected = (
                source_pos[0] * scale,
                -source_pos[1] * scale,
                source_pos[2] * scale,
            )
            error = max(abs(actual[axis] - expected[axis]) for axis in range(3))
            max_error = max(max_error, error)
            failures += int(error > tolerance)
        point_matches = failures == 0
        point_check = {
            "performed": True,
            "sample_count": len(indices),
            "max_component_error": max_error,
            "failures": failures,
            "matches": point_matches,
        }

    return {
        "input_node_path": input_node_path,
        "output_node_path": output_node_path,
        "convention": "UE=(H.x,-H.y,H.z)*scale",
        "scale": scale,
        "tolerance": tolerance,
        "input_bbox": input_bbox,
        "expected_output_bbox": expected_bbox,
        "actual_output_bbox": output_bbox,
        "bbox_matches": bbox_matches,
        "point_mapping": point_check,
        "axis_mapping_matches": point_matches if point_matches is not None else bbox_matches,
    }


register_handler("diagnostics.analyze_alembic_output", analyze_alembic_output)
register_handler("diagnostics.check_houdini_to_ue_space", check_houdini_to_ue_space)
