"""Read-only mesh diagnostics: topology, UV quality, and ray casts.

Every result is in the SOP's own space; the containing object's transform is
not applied. None of these handlers modify the scene.
"""

from __future__ import annotations

# Built-in
import math
from collections.abc import Callable
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_handler
from fxhoudinimcp_server.handlers.diagnostics_handlers import _get_sop_geo, _percentile, _rounded

# A polygon whose UV scale falls below this fraction of the group median (or
# below the absolute floor) has collapsed to a line or a point in UV space.
_DEGENERATE_UV_SCALE_FRACTION = 1e-3
_DEGENERATE_UV_SCALE_FLOOR = 1e-6


###### diagnostics.mesh_topology_report

def _polygons_by_piece(geo: hou.Geometry, piece_attrib: str | None) -> dict[Any, dict[str, Any]]:
    """Group closed-polygon point lists, open curves, and other prims by piece."""
    attrib = None
    if piece_attrib:
        attrib = geo.findPrimAttrib(piece_attrib)
        if attrib is None:
            raise hou.OperationFailed(f"Primitive attribute not found: {piece_attrib}")
    pieces: dict[Any, dict[str, Any]] = {}
    for prim in geo.iterPrims():
        key = prim.attribValue(attrib) if attrib is not None else None
        piece = pieces.setdefault(key, {"polygons": [], "open_curves": 0, "other_prims": 0})
        if prim.type() != hou.primType.Polygon:
            piece["other_prims"] += 1
        elif prim.isClosed():
            piece["polygons"].append([point.number() for point in prim.points()])
        else:
            piece["open_curves"] += 1
    return pieces


def _topology_metrics(polygons: list[list[int]], positions: list[tuple[float, float, float]]) -> dict[str, Any]:
    """Connectivity, edge manifoldness, area, and enclosed volume of polygons.

    Faces connect only through shared point numbers, so unfused seam points
    show up as boundary edges. Houdini's front faces wind clockwise, which
    makes the signed volume of an outward-facing closed shell negative.
    """
    parent: dict[int, int] = {}

    def find(point: int) -> int:
        root = point
        while parent.setdefault(root, root) != root:
            root = parent[root]
        while parent[point] != root:
            parent[point], point = root, parent[point]
        return root

    edge_use: dict[tuple[int, int], int] = {}
    area = 0.0
    volume6 = 0.0
    for points in polygons:
        for a, b in zip(points, points[1:] + points[:1], strict=True):
            edge = (a, b) if a < b else (b, a)
            edge_use[edge] = edge_use.get(edge, 0) + 1
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[root_a] = root_b
        x0, y0, z0 = positions[points[0]]
        for i in range(1, len(points) - 1):
            x1, y1, z1 = positions[points[i]]
            x2, y2, z2 = positions[points[i + 1]]
            ax, ay, az = x1 - x0, y1 - y0, z1 - z0
            bx, by, bz = x2 - x0, y2 - y0, z2 - z0
            area += 0.5 * math.sqrt(
                (ay * bz - az * by) ** 2 + (az * bx - ax * bz) ** 2 + (ax * by - ay * bx) ** 2
            )
            volume6 += x0 * (y1 * z2 - z1 * y2) + y0 * (z1 * x2 - x1 * z2) + z0 * (x1 * y2 - y1 * x2)

    boundary = sum(1 for uses in edge_use.values() if uses == 1)
    nonmanifold = sum(1 for uses in edge_use.values() if uses > 2)
    closed = bool(polygons) and boundary == 0 and nonmanifold == 0
    return {
        "polygons": len(polygons),
        "components": len({find(point) for point in list(parent)}),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "closed": closed,
        "area": round(area, 6),
        "volume": round(abs(volume6) / 6.0, 6) if closed else None,
        "normals_outward": (volume6 < 0.0) if closed else None,
    }


def mesh_topology_report(
    node_path: str,
    piece_attrib: str | None = None,
    max_pieces: int = 20,
) -> dict[str, Any]:
    """Report closedness, connectivity, open curves, and volume, optionally per piece."""
    geo = _get_sop_geo(node_path)
    flat = geo.pointFloatAttribValues("P")
    positions = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)]
    pieces = _polygons_by_piece(geo, piece_attrib)

    result: dict[str, Any] = {
        "node_path": node_path,
        "open_curves": sum(piece["open_curves"] for piece in pieces.values()),
        "other_prims": sum(piece["other_prims"] for piece in pieces.values()),
    }
    result.update(
        _topology_metrics([poly for piece in pieces.values() for poly in piece["polygons"]], positions)
    )
    if not piece_attrib:
        return result

    rows = []
    for key, piece in pieces.items():
        row = {"piece": key, "open_curves": piece["open_curves"], "other_prims": piece["other_prims"]}
        row.update(_topology_metrics(piece["polygons"], positions))
        rows.append(row)
    problems = [
        row for row in rows
        if row["components"] != 1 or not row["closed"] or row["open_curves"] or row["other_prims"]
    ]
    volumes = sorted(row["volume"] for row in rows if row["volume"] is not None)
    result["pieces"] = {
        "attrib": piece_attrib,
        "count": len(rows),
        "with_problems": len(problems),
        "problems": problems[:max_pieces],
        "closed_volume": {
            "pieces": len(volumes),
            "total": round(sum(volumes), 6),
            "min": _percentile(volumes, 0.0),
            "p10": _percentile(volumes, 0.1),
            "p50": _percentile(volumes, 0.5),
            "p90": _percentile(volumes, 0.9),
            "max": _percentile(volumes, 1.0),
        },
    }
    return result


register_handler("diagnostics.mesh_topology_report", mesh_topology_report)


###### diagnostics.uv_quality_report

def _uv_reader(geo: hou.Geometry, uv_attrib: str) -> Callable[[hou.Vertex], tuple]:
    """Return a vertex -> UV tuple reader for a vertex or point UV attribute."""
    attrib = geo.findVertexAttrib(uv_attrib)
    if attrib is not None:
        return lambda vertex: vertex.attribValue(attrib)
    attrib = geo.findPointAttrib(uv_attrib)
    if attrib is not None:
        return lambda vertex: vertex.point().attribValue(attrib)
    raise hou.OperationFailed(f"UV attribute not found on vertices or points: {uv_attrib}")


def _uv_group_stats(prims: tuple[hou.Prim, ...], read_uv: Callable[[hou.Vertex], tuple]) -> dict[str, Any]:
    """Degenerate, flipped, and texel-scale statistics for closed polygons."""
    skipped = 0
    samples: list[tuple[float, float]] = []  # (signed UV area, 3D area)
    uv_min = [math.inf, math.inf]
    uv_max = [-math.inf, -math.inf]
    for prim in prims:
        if prim.type() != hou.primType.Polygon or not prim.isClosed() or prim.numVertices() < 3:
            skipped += 1
            continue
        uvs = [read_uv(vertex) for vertex in prim.vertices()]
        twice_area = 0.0
        for (u0, v0, *_), (u1, v1, *_) in zip(uvs, uvs[1:] + uvs[:1], strict=True):
            twice_area += u0 * v1 - u1 * v0
        samples.append((0.5 * twice_area, prim.intrinsicValue("measuredarea")))
        for u, v, *_ in uvs:
            uv_min = [min(uv_min[0], u), min(uv_min[1], v)]
            uv_max = [max(uv_max[0], u), max(uv_max[1], v)]

    # Texel scale: UV units per scene unit, i.e. sqrt(UV area / 3D area).
    scaled = [(math.sqrt(abs(uv_area) / area), uv_area) for uv_area, area in samples if area > 0.0]
    median = _percentile(sorted(scale for scale, _ in scaled), 0.5) or 0.0
    limit = max(median * _DEGENERATE_UV_SCALE_FRACTION, _DEGENERATE_UV_SCALE_FLOOR)
    live = [(scale, uv_area) for scale, uv_area in scaled if scale > limit]
    positive = sum(1 for _, uv_area in live if uv_area > 0.0)
    live_scales = sorted(scale for scale, _ in live)
    p1 = _percentile(live_scales, 0.01)
    p99 = _percentile(live_scales, 0.99)
    return {
        "polygons": len(samples),
        "skipped_non_polygons": skipped,
        "zero_3d_area": len(samples) - len(scaled),
        "degenerate_uv": len(scaled) - len(live),
        "flipped_uv": min(positive, len(live) - positive),
        "texel_scale": {"p1": p1, "p50": _percentile(live_scales, 0.5), "p99": p99},
        "scale_spread_p99_p1": round(p99 / p1, 4) if p1 else None,
        "uv_bounds": {"min": _rounded(uv_min), "max": _rounded(uv_max)} if samples else None,
    }


def uv_quality_report(
    node_path: str,
    uv_attrib: str = "uv",
    groups: list[str] | None = None,
) -> dict[str, Any]:
    """Per primitive-group UV health: collapsed, flipped, and texel-scale spread."""
    geo = _get_sop_geo(node_path)
    read_uv = _uv_reader(geo, uv_attrib)
    reports = []
    for pattern in groups or ["*"]:
        report = {"group": pattern}
        report.update(_uv_group_stats(geo.globPrims(pattern), read_uv))
        reports.append(report)
    return {"node_path": node_path, "uv_attrib": uv_attrib, "groups": reports}


register_handler("diagnostics.uv_quality_report", uv_quality_report)


###### diagnostics.ray_intersect

def _grid_origins(center: hou.Vector3, size: float, resolution: int, direction: hou.Vector3) -> list[hou.Vector3]:
    """Cell-centre ray origins on a square centred on ``center``, facing ``direction``."""
    reference = hou.Vector3(1, 0, 0) if abs(direction[0]) < 0.9 else hou.Vector3(0, 1, 0)
    u_axis = direction.cross(reference).normalized()
    v_axis = direction.cross(u_axis)
    origins = []
    for i in range(resolution):
        for j in range(resolution):
            u = ((i + 0.5) / resolution - 0.5) * size
            v = ((j + 0.5) / resolution - 0.5) * size
            origins.append(center + u_axis * u + v_axis * v)
    return origins


def ray_intersect(
    node_path: str,
    direction: list[float] | None = None,
    origins: list[list[float]] | None = None,
    grid_center: list[float] | None = None,
    grid_size: float | None = None,
    grid_resolution: int = 10,
    max_distance: float | None = None,
    label_attrib: str | None = None,
    max_results: int = 200,
) -> dict[str, Any]:
    """Cast rays at SOP geometry from explicit origins or a square grid."""
    geo = _get_sop_geo(node_path)
    ray_direction = hou.Vector3(direction or (0.0, -1.0, 0.0))
    if ray_direction.length() == 0.0:
        raise hou.OperationFailed("direction must be non-zero")
    ray_direction = ray_direction.normalized()
    if (origins is None) == (grid_center is None):
        raise hou.OperationFailed("Pass either origins or grid_center with grid_size")
    if grid_center is not None:
        if grid_size is None or grid_size <= 0.0 or grid_resolution < 1:
            raise hou.OperationFailed("grid_size must be positive and grid_resolution at least 1")
        ray_origins = _grid_origins(hou.Vector3(grid_center), grid_size, grid_resolution, ray_direction)
    else:
        ray_origins = [hou.Vector3(origin) for origin in origins]
    label = None
    if label_attrib:
        label = geo.findPrimAttrib(label_attrib)
        if label is None:
            raise hou.OperationFailed(f"Primitive attribute not found: {label_attrib}")

    rays = []
    distances = []
    label_hits: dict[str, int] = {}
    for origin in ray_origins:
        position, normal, uvw = hou.Vector3(), hou.Vector3(), hou.Vector3()
        prim_number = geo.intersect(
            origin, ray_direction, position, normal, uvw,
            max_hit=max_distance if max_distance is not None else 1e18,
        )
        row: dict[str, Any] = {"origin": _rounded(origin), "hit": prim_number >= 0}
        if prim_number >= 0:
            distance = (position - origin).length()
            distances.append(distance)
            row.update(
                position=_rounded(position),
                normal=_rounded(normal),
                distance=round(distance, 6),
                prim=prim_number,
            )
            if label is not None:
                value = str(geo.prim(prim_number).attribValue(label))
                row["label"] = value
                label_hits[value] = label_hits.get(value, 0) + 1
        rays.append(row)

    misses = [row["origin"] for row in rays if not row["hit"]]
    return {
        "node_path": node_path,
        "direction": _rounded(ray_direction),
        "ray_count": len(rays),
        "hits": len(distances),
        "misses": len(misses),
        "distance": {"min": round(min(distances), 6), "max": round(max(distances), 6)} if distances else None,
        "label_hits": label_hits or None,
        "miss_origins": misses[:max_results],
        "rays": rays[:max_results],
        "truncated": len(rays) > max_results,
    }


register_handler("diagnostics.ray_intersect", ray_intersect)
