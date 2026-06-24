"""Geometry (SOP) handlers for FXHoudini-MCP.

Each handler reads or modifies geometry on SOP nodes.
All functions run on the main thread via the dispatcher.
"""

from __future__ import annotations

# Built-in
import random
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


def _vec_to_list(v: Any) -> Any:
    """Convert hou.Vector2/3/4, hou.Color, etc. to a plain list."""
    if isinstance(v, (hou.Vector2, hou.Vector3, hou.Vector4,
                       hou.Quaternion, hou.Color)):
        return list(v)
    if isinstance(v, hou.Matrix3):
        return [list(v.at(r, c) for c in range(3)) for r in range(3)]
    if isinstance(v, hou.Matrix4):
        return [list(v.at(r, c) for c in range(4)) for r in range(4)]
    return v


def _attrib_meta(attrib: hou.Attrib) -> dict[str, Any]:
    """Return JSON-safe metadata for an attribute."""
    return {
        "name": attrib.name(),
        "type": attrib.dataType().name(),
        "size": attrib.size(),
        "is_array": attrib.isArrayType(),
    }


def _attrib_class_obj(geo: hou.Geometry, attrib_class: str) -> Any:
    """Map a string class name to the hou.attribType enum value."""
    mapping = {
        "point": hou.attribType.Point,
        "prim": hou.attribType.Prim,
        "vertex": hou.attribType.Vertex,
        "detail": hou.attribType.Global,
        "global": hou.attribType.Global,
    }
    cls = mapping.get(attrib_class.lower())
    if cls is None:
        raise ValueError(f"Invalid attrib_class: {attrib_class!r}. "
                         f"Use one of {list(mapping.keys())}")
    return cls


###### geometry.get_geometry_info

def _get_geometry_info(*, node_path: str) -> dict[str, Any]:
    """Return summary information about a SOP node's geometry."""
    geo = _get_sop_geo(node_path)

    # Attribute lists per class
    attribs: dict[str, list[dict]] = {}
    for label, getter in [
        ("point", geo.pointAttribs),
        ("prim", geo.primAttribs),
        ("vertex", geo.vertexAttribs),
        ("detail", geo.globalAttribs),
    ]:
        attribs[label] = [_attrib_meta(a) for a in getter()]

    # Prim type breakdown — indexed access via geo.prim() so large meshes
    # never materialize the full prim tuple. Sampled when over the limit.
    total_prims = geo.intrinsicValue("primitivecount")
    _PRIM_SAMPLE_LIMIT = 2_500
    prim_types: dict[str, int] = {}
    prim_sample_note: str | None = None
    if total_prims <= _PRIM_SAMPLE_LIMIT:
        for i in range(total_prims):
            t = geo.prim(i).type().name()
            prim_types[t] = prim_types.get(t, 0) + 1
    else:
        step = total_prims / _PRIM_SAMPLE_LIMIT
        for i in range(_PRIM_SAMPLE_LIMIT):
            t = geo.prim(int(i * step)).type().name()
            prim_types[t] = prim_types.get(t, 0) + 1
        # Scale counts back up to approximate totals
        scale = total_prims / _PRIM_SAMPLE_LIMIT
        prim_types = {k: int(v * scale) for k, v in prim_types.items()}
        prim_sample_note = f"sampled {_PRIM_SAMPLE_LIMIT}/{total_prims} prims"

    bbox = geo.boundingBox()

    result: dict[str, Any] = {
        "node_path": node_path,
        "num_points": geo.intrinsicValue("pointcount"),
        "num_prims": geo.intrinsicValue("primitivecount"),
        "num_vertices": geo.intrinsicValue("vertexcount"),
        "attributes": attribs,
        "bounding_box": {
            "min": list(bbox.minvec()),
            "max": list(bbox.maxvec()),
            "size": list(bbox.sizevec()),
            "center": list(bbox.center()),
        },
        "prim_type_breakdown": prim_types,
    }
    if prim_sample_note:
        result["prim_type_breakdown_note"] = prim_sample_note
    return result

register_handler("geometry.get_geometry_info", _get_geometry_info)


###### geometry.get_points

def _get_points(
    *,
    node_path: str,
    attributes: list[str] | None = None,
    start: int = 0,
    count: int = 1000,
    group: str | None = None,
) -> dict[str, Any]:
    """Read point positions and attributes with pagination.

    Uses indexed access (geo.point) so the cost scales with the page
    size, never with the total point count.
    """
    geo = _get_sop_geo(node_path)

    if attributes is None:
        attributes = ["P"]

    if group:
        pt_group = geo.findPointGroup(group)
        if pt_group is None:
            raise hou.OperationFailed(f"Point group not found: {group}")
        points = pt_group.points()
        total = len(points)
        end = min(start + count, total)
        page = points[start:end]
    else:
        total = geo.intrinsicValue("pointcount")
        end = min(start + count, total)
        page = [geo.point(i) for i in range(max(start, 0), end)]

    found = {name: geo.findPointAttrib(name) is not None for name in attributes}

    rows: list[dict[str, Any]] = []
    for pt in page:
        row: dict[str, Any] = {"index": pt.number()}
        for attr_name in attributes:
            row[attr_name] = (
                _vec_to_list(pt.attribValue(attr_name))
                if found[attr_name]
                else None
            )
        rows.append(row)

    return {
        "node_path": node_path,
        "total_points": total,
        "start": start,
        "count": len(rows),
        "has_more": end < total,
        "points": rows,
    }

register_handler("geometry.get_points", _get_points)


###### geometry.get_prims

def _get_prims(
    *,
    node_path: str,
    attributes: list[str] | None = None,
    start: int = 0,
    count: int = 1000,
    group: str | None = None,
) -> dict[str, Any]:
    """Read primitive data and attributes with pagination."""
    geo = _get_sop_geo(node_path)

    if group:
        pr_group = geo.findPrimGroup(group)
        if pr_group is None:
            raise hou.OperationFailed(f"Prim group not found: {group}")
        prims = pr_group.prims()
        total = len(prims)
        end = min(start + count, total)
        page = prims[start:end]
    else:
        # Indexed access keeps the cost proportional to the page size.
        total = geo.intrinsicValue("primitivecount")
        end = min(start + count, total)
        page = [geo.prim(i) for i in range(max(start, 0), end)]

    # If no attributes specified, gather all prim attribute names
    if attributes is None:
        attributes = [a.name() for a in geo.primAttribs()]

    rows: list[dict[str, Any]] = []
    for prim in page:
        row: dict[str, Any] = {
            "index": prim.number(),
            "type": prim.type().name(),
            "num_vertices": prim.numVertices(),
        }
        for attr_name in attributes:
            attrib = geo.findPrimAttrib(attr_name)
            if attrib is None:
                row[attr_name] = None
                continue
            val = prim.attribValue(attr_name)
            row[attr_name] = _vec_to_list(val)
        rows.append(row)

    return {
        "node_path": node_path,
        "total_prims": total,
        "start": start,
        "count": len(rows),
        "has_more": end < total,
        "prims": rows,
    }

register_handler("geometry.get_prims", _get_prims)


###### geometry.get_attrib_values

def _get_attrib_values(
    *,
    node_path: str,
    attrib_name: str,
    attrib_class: str = "point",
    start: int = 0,
    count: int = 200,
) -> dict[str, Any]:
    """Read attribute values as a flat array with pagination.

    Values are element-major (e.g. for a float3 attribute with tuple_size=3,
    every 3 consecutive values belong to one element).  Use start/count to
    page through large attributes without blowing the LLM context.
    """
    geo = _get_sop_geo(node_path)

    cls = attrib_class.lower()
    if cls == "point":
        attrib = geo.findPointAttrib(attrib_name)
        if attrib is None:
            raise hou.OperationFailed(
                f"Point attribute '{attrib_name}' not found on {node_path}")
        all_values = geo.pointFloatAttribValues(attrib_name) if attrib.dataType() == hou.attribData.Float \
            else geo.pointIntAttribValues(attrib_name) if attrib.dataType() == hou.attribData.Int \
            else geo.pointStringAttribValues(attrib_name)
    elif cls == "prim":
        attrib = geo.findPrimAttrib(attrib_name)
        if attrib is None:
            raise hou.OperationFailed(
                f"Prim attribute '{attrib_name}' not found on {node_path}")
        all_values = geo.primFloatAttribValues(attrib_name) if attrib.dataType() == hou.attribData.Float \
            else geo.primIntAttribValues(attrib_name) if attrib.dataType() == hou.attribData.Int \
            else geo.primStringAttribValues(attrib_name)
    elif cls == "vertex":
        attrib = geo.findVertexAttrib(attrib_name)
        if attrib is None:
            raise hou.OperationFailed(
                f"Vertex attribute '{attrib_name}' not found on {node_path}")
        all_values = geo.vertexFloatAttribValues(attrib_name) if attrib.dataType() == hou.attribData.Float \
            else geo.vertexIntAttribValues(attrib_name) if attrib.dataType() == hou.attribData.Int \
            else geo.vertexStringAttribValues(attrib_name)
    elif cls in ("detail", "global"):
        attrib = geo.findGlobalAttrib(attrib_name)
        if attrib is None:
            raise hou.OperationFailed(
                f"Detail attribute '{attrib_name}' not found on {node_path}")
        val = geo.attribValue(attrib_name)
        return {
            "node_path": node_path,
            "attrib_name": attrib_name,
            "attrib_class": attrib_class,
            "size": attrib.size(),
            "type": attrib.dataType().name(),
            "value": _vec_to_list(val),
        }
    else:
        raise ValueError(f"Invalid attrib_class: {attrib_class!r}")

    tuple_size = max(attrib.size(), 1)
    total_elements = len(all_values) // tuple_size
    # Clamp page to element boundaries
    start_elem = max(0, min(start, total_elements))
    end_elem = min(start_elem + max(1, count), total_elements)
    page = list(all_values[start_elem * tuple_size : end_elem * tuple_size])

    return {
        "node_path": node_path,
        "attrib_name": attrib_name,
        "attrib_class": attrib_class,
        "tuple_size": tuple_size,
        "type": attrib.dataType().name(),
        "total_elements": total_elements,
        "start": start_elem,
        "count": end_elem - start_elem,
        "has_more": end_elem < total_elements,
        "values": page,
    }

register_handler("geometry.get_attrib_values", _get_attrib_values)


###### geometry.set_detail_attrib

def _set_detail_attrib(
    *,
    node_path: str,
    attrib_name: str,
    value: Any,
) -> dict[str, Any]:
    """Set a detail (global) attribute via an appended Attribute Create SOP.

    SOP geometry cannot be edited in place from outside a node cook
    (hou.SopNode has no setGeometry), so this wires an attribcreate node
    after *node_path* and moves the display/render flags to it.
    """
    node = hou.node(node_path)
    if node is None:
        raise hou.OperationFailed(f"Node not found: {node_path}")
    if node.geometry() is None:
        raise hou.OperationFailed(f"Node has no geometry: {node_path}")

    attrib_node = node.parent().createNode("attribcreate", f"set_{attrib_name}")
    attrib_node.setInput(0, node)
    attrib_node.parm("numattr").set(1)
    attrib_node.parm("class1").set("detail")
    attrib_node.parm("name1").set(attrib_name)

    if isinstance(value, str):
        attrib_node.parm("type1").set("index")
        attrib_node.parm("string1").set(value)
    elif isinstance(value, bool):
        attrib_node.parm("type1").set("int")
        attrib_node.parm("value1v1").set(int(value))
    elif isinstance(value, int):
        attrib_node.parm("type1").set("int")
        attrib_node.parm("value1v1").set(value)
    elif isinstance(value, float):
        attrib_node.parm("type1").set("float")
        attrib_node.parm("value1v1").set(value)
    elif isinstance(value, (list, tuple)) and 1 <= len(value) <= 4:
        attrib_node.parm("type1").set("float")
        attrib_node.parm("size1").set(len(value))
        for index, component in enumerate(value):
            attrib_node.parm(f"value1v{index + 1}").set(float(component))
    else:
        attrib_node.destroy()
        raise ValueError(f"Unsupported value type: {type(value).__name__}")

    attrib_node.setDisplayFlag(True)
    attrib_node.setRenderFlag(True)

    # Read the value back from the cooked geometry so the result reports
    # what actually happened rather than what was requested.
    applied = attrib_node.geometry().attribValue(attrib_name)

    return {
        "node_path": node_path,
        "attrib_node_path": attrib_node.path(),
        "attrib_name": attrib_name,
        "value": _vec_to_list(applied),
        "success": True,
    }

register_handler("geometry.set_detail_attrib", _set_detail_attrib)


###### geometry.get_groups

def _get_groups(*, node_path: str) -> dict[str, Any]:
    """List all point/prim/edge groups with membership counts."""
    geo = _get_sop_geo(node_path)

    groups: dict[str, list[dict[str, Any]]] = {
        "point_groups": [],
        "prim_groups": [],
        "edge_groups": [],
    }

    for grp in geo.pointGroups():
        groups["point_groups"].append({
            "name": grp.name(),
            "count": len(grp.points()),
        })

    for grp in geo.primGroups():
        groups["prim_groups"].append({
            "name": grp.name(),
            "count": len(grp.prims()),
        })

    for grp in geo.edgeGroups():
        groups["edge_groups"].append({
            "name": grp.name(),
            "count": len(grp.edges()),
        })

    return {
        "node_path": node_path,
        **groups,
    }

register_handler("geometry.get_groups", _get_groups)


###### geometry.get_group_members

def _get_group_members(
    *,
    node_path: str,
    group_name: str,
    group_type: str = "point",
    start: int = 0,
    count: int = 5000,
) -> dict[str, Any]:
    """Get element indices belonging to a group, with pagination."""
    geo = _get_sop_geo(node_path)

    gt = group_type.lower()
    if gt == "point":
        grp = geo.findPointGroup(group_name)
        if grp is None:
            raise hou.OperationFailed(
                f"Point group '{group_name}' not found on {node_path}")
        all_indices = [pt.number() for pt in grp.points()]
    elif gt == "prim":
        grp = geo.findPrimGroup(group_name)
        if grp is None:
            raise hou.OperationFailed(
                f"Prim group '{group_name}' not found on {node_path}")
        all_indices = [pr.number() for pr in grp.prims()]
    elif gt == "edge":
        grp = geo.findEdgeGroup(group_name)
        if grp is None:
            raise hou.OperationFailed(
                f"Edge group '{group_name}' not found on {node_path}")
        all_indices = [[e.points()[0].number(), e.points()[1].number()]
                       for e in grp.edges()]
    else:
        raise ValueError(f"Invalid group_type: {group_type!r}. "
                         f"Use 'point', 'prim', or 'edge'.")

    total = len(all_indices)
    end = min(start + max(1, count), total)
    page = all_indices[start:end]

    return {
        "node_path": node_path,
        "group_name": group_name,
        "group_type": group_type,
        "total_count": total,
        "start": start,
        "count": len(page),
        "has_more": end < total,
        "members": page,
    }

register_handler("geometry.get_group_members", _get_group_members)


###### geometry.get_bounding_box

def _get_bounding_box(*, node_path: str) -> dict[str, Any]:
    """Get axis-aligned bounding box for a SOP node's geometry."""
    geo = _get_sop_geo(node_path)
    bbox = geo.boundingBox()

    return {
        "node_path": node_path,
        "min": list(bbox.minvec()),
        "max": list(bbox.maxvec()),
        "size": list(bbox.sizevec()),
        "center": list(bbox.center()),
    }

register_handler("geometry.get_bounding_box", _get_bounding_box)


###### geometry.get_attribute_info

def _get_attribute_info(
    *,
    node_path: str,
    attrib_name: str,
    attrib_class: str = "point",
) -> dict[str, Any]:
    """Detailed attribute info: type, size, default value."""
    geo = _get_sop_geo(node_path)

    cls = attrib_class.lower()
    finders = {
        "point": geo.findPointAttrib,
        "prim": geo.findPrimAttrib,
        "vertex": geo.findVertexAttrib,
        "detail": geo.findGlobalAttrib,
        "global": geo.findGlobalAttrib,
    }
    finder = finders.get(cls)
    if finder is None:
        raise ValueError(f"Invalid attrib_class: {attrib_class!r}")

    attrib = finder(attrib_name)
    if attrib is None:
        raise hou.OperationFailed(
            f"{attrib_class.title()} attribute '{attrib_name}' "
            f"not found on {node_path}")

    default = attrib.defaultValue()

    return {
        "node_path": node_path,
        "attrib_name": attrib_name,
        "attrib_class": attrib_class,
        "type": attrib.dataType().name(),
        "size": attrib.size(),
        "is_array": attrib.isArrayType(),
        "default_value": _vec_to_list(default),
        "qualifier": attrib.qualifier() if hasattr(attrib, "qualifier") else None,
        "type_name": attrib.dataType().name(),
    }

register_handler("geometry.get_attribute_info", _get_attribute_info)


###### geometry.sample_geometry

def _sample_geometry(
    *,
    node_path: str,
    sample_count: int = 100,
    seed: int = 0,
) -> dict[str, Any]:
    """Smart sampling: get N evenly distributed points from geometry."""
    geo = _get_sop_geo(node_path)

    total = geo.intrinsicValue("pointcount")

    if total == 0:
        return {
            "node_path": node_path,
            "total_points": 0,
            "sample_count": 0,
            "points": [],
        }

    # Determine indices to sample
    actual_count = min(sample_count, total)

    if actual_count >= total:
        # Return all points
        sampled_indices = list(range(total))
    else:
        # Evenly spaced sampling with optional seed for reproducibility
        rng = random.Random(seed)
        step = total / actual_count
        # Start with evenly spaced, then jitter slightly for variety
        sampled_indices = sorted(set(
            min(int(i * step + rng.uniform(0, step * 0.5)), total - 1)
            for i in range(actual_count)
        ))

    # Gather attributes — indexed access keeps the cost O(sample_count)
    point_attrib_names = [a.name() for a in geo.pointAttribs()]

    rows: list[dict[str, Any]] = []
    for idx in sampled_indices:
        pt = geo.point(idx)
        row: dict[str, Any] = {"index": pt.number()}
        for attr_name in point_attrib_names:
            val = pt.attribValue(attr_name)
            row[attr_name] = _vec_to_list(val)
        rows.append(row)

    return {
        "node_path": node_path,
        "total_points": total,
        "sample_count": len(rows),
        "seed": seed,
        "attributes": point_attrib_names,
        "points": rows,
    }

register_handler("geometry.sample_geometry", _sample_geometry)


###### geometry.get_prim_intrinsics

def _get_prim_intrinsics(
    *,
    node_path: str,
    prim_index: int | None = None,
) -> dict[str, Any]:
    """Get intrinsic values for primitives.

    If prim_index is None, return a summary across all primitives.
    Otherwise return intrinsics for the specified primitive.
    """
    geo = _get_sop_geo(node_path)
    total_prims = geo.intrinsicValue("primitivecount")

    if prim_index is not None:
        if prim_index < 0 or prim_index >= total_prims:
            raise hou.OperationFailed(
                f"Prim index {prim_index} out of range "
                f"(0..{total_prims - 1}) on {node_path}")
        prim = geo.prim(prim_index)
        intrinsic_names = prim.intrinsicNames()
        intrinsics: dict[str, Any] = {}
        for name in intrinsic_names:
            val = prim.intrinsicValue(name)
            intrinsics[name] = _vec_to_list(val)

        return {
            "node_path": node_path,
            "prim_index": prim_index,
            "prim_type": prim.type().name(),
            "intrinsics": intrinsics,
        }

    # Summary mode: aggregate intrinsics across (a sample of) the prims.
    # Reading every intrinsic of every prim is O(names * prims) HOM calls
    # — 14+ seconds on a 250k-prim mesh — so cap the statistics sample.
    if total_prims == 0:
        return {
            "node_path": node_path,
            "total_prims": 0,
            "summary": {},
        }

    _SUMMARY_SAMPLE_LIMIT = 1_000
    if total_prims <= _SUMMARY_SAMPLE_LIMIT:
        sample_prims = [geo.prim(i) for i in range(total_prims)]
        sample_note = None
    else:
        step = total_prims / _SUMMARY_SAMPLE_LIMIT
        sample_prims = [
            geo.prim(int(i * step)) for i in range(_SUMMARY_SAMPLE_LIMIT)
        ]
        sample_note = (
            f"statistics sampled from {_SUMMARY_SAMPLE_LIMIT}/{total_prims} prims"
        )

    sample_prim = sample_prims[0]
    intrinsic_names = sample_prim.intrinsicNames()

    summary: dict[str, Any] = {}
    for name in intrinsic_names:
        try:
            first_val = sample_prim.intrinsicValue(name)
            if isinstance(first_val, (int, float)):
                # Compute min/max/avg for numeric intrinsics
                vals = [p.intrinsicValue(name) for p in sample_prims]
                summary[name] = {
                    "min": min(vals),
                    "max": max(vals),
                    "avg": sum(vals) / len(vals),
                    "sample": first_val,
                }
            elif isinstance(first_val, str):
                # Collect unique string values
                unique = set()
                for p in sample_prims:
                    unique.add(p.intrinsicValue(name))
                    if len(unique) > 20:
                        break
                summary[name] = {
                    "unique_values": sorted(unique)[:20],
                    "sample": first_val,
                }
            else:
                summary[name] = {
                    "sample": _vec_to_list(first_val),
                }
        except Exception:
            summary[name] = {"sample": None, "error": "Could not read"}

    result = {
        "node_path": node_path,
        "total_prims": total_prims,
        "intrinsic_names": list(intrinsic_names),
        "summary": summary,
    }
    if sample_note:
        result["summary_note"] = sample_note
    return result

register_handler("geometry.get_prim_intrinsics", _get_prim_intrinsics)


###### geometry.find_nearest_point

def _find_nearest_point(
    *,
    node_path: str,
    position: list[float],
    max_results: int = 1,
) -> dict[str, Any]:
    """Find nearest point(s) to a given position."""
    geo = _get_sop_geo(node_path)

    if len(position) != 3:
        raise ValueError("position must be a list of 3 floats [x, y, z]")

    total = geo.intrinsicValue("pointcount")
    if total == 0:
        return {
            "node_path": node_path,
            "position": position,
            "results": [],
        }

    k = max(max_results, 1)
    flat = geo.pointFloatAttribValues("P")

    try:
        import numpy as np

        coords = np.array(flat, dtype=np.float64).reshape(-1, 3)
        squared = ((coords - np.array(position)) ** 2).sum(axis=1)
        if k >= len(squared):
            top_indices = np.argsort(squared)
        else:
            top_indices = np.argpartition(squared, k)[:k]
            top_indices = top_indices[np.argsort(squared[top_indices])]
        top = [(float(squared[i]) ** 0.5, int(i)) for i in top_indices[:k]]
    except ImportError:
        px, py, pz = position
        distances: list[tuple[float, int]] = []
        for i in range(total):
            dx = flat[i * 3] - px
            dy = flat[i * 3 + 1] - py
            dz = flat[i * 3 + 2] - pz
            distances.append((dx * dx + dy * dy + dz * dz, i))
        distances.sort(key=lambda x: x[0])
        top = [(d**0.5, i) for d, i in distances[:k]]

    results: list[dict[str, Any]] = []
    for dist, idx in top:
        results.append({
            "index": idx,
            "position": list(flat[idx * 3 : idx * 3 + 3]),
            "distance": dist,
        })

    return {
        "node_path": node_path,
        "query_position": position,
        "max_results": max_results,
        "results": results,
    }

register_handler("geometry.find_nearest_point", _find_nearest_point)


###### Helpers — frame-based attribute cooking

def _find_attrib(geo: hou.Geometry, attrib_name: str, attrib_class: str) -> Any:
    """Find an attribute by class, raising ValueError if it is missing.

    Args:
        geo: Cooked geometry to search.
        attrib_name: Attribute name.
        attrib_class: "point", "prim", "vertex", or "detail"/"global".

    Returns:
        The matching hou.Attrib.

    Raises:
        ValueError: if attrib_class is invalid or the attribute is absent.
    """
    cls = attrib_class.lower()
    finders = {
        "point": geo.findPointAttrib,
        "prim": geo.findPrimAttrib,
        "vertex": geo.findVertexAttrib,
        "detail": geo.findGlobalAttrib,
        "global": geo.findGlobalAttrib,
    }
    finder = finders.get(cls)
    if finder is None:
        raise ValueError(
            f"Invalid attrib_class: {attrib_class!r}. "
            f"Use one of {list(finders.keys())}"
        )
    attrib = finder(attrib_name)
    if attrib is None:
        raise ValueError(
            f"{cls.title()} attribute '{attrib_name}' not found on geometry"
        )
    return attrib


def _read_flat_attrib(
    geo: hou.Geometry, attrib_name: str, attrib_class: str
) -> tuple[list[float], int, int]:
    """Read an attribute as a flat, element-major list of numbers.

    Mirrors the value-fetching done by ``geometry.get_attrib_values``: it uses
    the bulk ``*FloatAttribValues`` / ``*IntAttribValues`` accessors for
    point/prim/vertex classes and ``geo.attribValue`` for detail, then
    flattens any tuple components into a single sequence.

    Args:
        geo: Cooked geometry to read from.
        attrib_name: Attribute name.
        attrib_class: "point", "prim", "vertex", or "detail"/"global".

    Returns:
        A ``(values, tuple_size, count)`` triple where ``values`` is the flat
        element-major list, ``tuple_size`` is the per-element component count,
        and ``count`` is the number of elements.

    Raises:
        ValueError: if the class is invalid, the attribute is missing, or the
            data type is not numeric (float/int).
    """
    attrib = _find_attrib(geo, attrib_name, attrib_class)
    tuple_size = max(attrib.size(), 1)
    data_type = attrib.dataType()
    cls = attrib_class.lower()

    if cls in ("detail", "global"):
        raw = geo.attribValue(attrib_name)
        flat = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        if not all(isinstance(component, (int, float)) for component in flat):
            raise ValueError(
                f"Attribute '{attrib_name}' is not numeric (float/int)"
            )
        return [float(component) for component in flat], tuple_size, 1

    bulk_getters = {
        "point": (geo.pointFloatAttribValues, geo.pointIntAttribValues),
        "prim": (geo.primFloatAttribValues, geo.primIntAttribValues),
        "vertex": (geo.vertexFloatAttribValues, geo.vertexIntAttribValues),
    }
    float_getter, int_getter = bulk_getters[cls]
    if data_type == hou.attribData.Float:
        all_values = float_getter(attrib_name)
    elif data_type == hou.attribData.Int:
        all_values = int_getter(attrib_name)
    else:
        raise ValueError(
            f"Attribute '{attrib_name}' has non-numeric type "
            f"{data_type.name()}; only float/int are supported"
        )

    flat = [float(value) for value in all_values]
    count = len(flat) // tuple_size if tuple_size else 0
    return flat, tuple_size, count


def _component_stats(
    flat: list[float], tuple_size: int
) -> tuple[list[float], list[float], list[float], int]:
    """Compute per-component min/max/mean over an element-major flat list.

    Args:
        flat: Element-major values (every ``tuple_size`` items is one element).
        tuple_size: Number of components per element.

    Returns:
        A ``(mins, maxs, means, count)`` tuple. The stat lists are empty when
        there are no elements.
    """
    count = len(flat) // tuple_size if tuple_size else 0
    if count == 0:
        return [], [], [], 0

    mins = [float("inf")] * tuple_size
    maxs = [float("-inf")] * tuple_size
    sums = [0.0] * tuple_size
    for element_index in range(count):
        base = element_index * tuple_size
        for component in range(tuple_size):
            value = flat[base + component]
            if value < mins[component]:
                mins[component] = value
            if value > maxs[component]:
                maxs[component] = value
            sums[component] += value
    means = [total / count for total in sums]
    return mins, maxs, means, count


def _max_abs_diff(a: list[float], b: list[float]) -> tuple[float, int, int]:
    """Compare two flat numeric lists elementwise.

    Args:
        a: First flat list.
        b: Second flat list.

    Returns:
        A ``(max_abs_diff, num_differing, num_compared)`` tuple where
        ``num_compared`` is ``min(len(a), len(b))``. If the lengths differ the
        surplus tail is ignored for the diff but the lengths can be inspected
        by the caller.
    """
    num_compared = min(len(a), len(b))
    max_diff = 0.0
    num_differing = 0
    for index in range(num_compared):
        diff = abs(a[index] - b[index])
        if diff > max_diff:
            max_diff = diff
        if diff > 0.0:
            num_differing += 1
    return max_diff, num_differing, num_compared


###### geometry.attribute_stats

def _attribute_stats(
    *,
    node_path: str,
    attrib_name: str,
    attrib_class: str = "point",
    frames: list[float] | None = None,
) -> dict[str, Any]:
    """Per-frame min/max/mean for an attribute across one or more frames.

    Cooks the node at each requested frame, reads the attribute, and reports
    per-component statistics. Useful for verifying that an animated attribute
    moves through the value range you expect over time.

    Args:
        node_path: SOP node path.
        attrib_name: Attribute name.
        attrib_class: "point", "prim", "vertex", or "detail".
        frames: Frame numbers to sample; defaults to the current frame.

    Raises:
        ValueError: if the attribute or class is invalid.
    """
    sample_frames = frames if frames else [hou.frame()]

    components = 0
    per_frame: list[dict[str, Any]] = []
    original_frame = hou.frame()
    try:
        for frame in sample_frames:
            hou.setFrame(frame)
            geo = _get_sop_geo(node_path)
            flat, tuple_size, _ = _read_flat_attrib(
                geo, attrib_name, attrib_class
            )
            components = tuple_size
            mins, maxs, means, count = _component_stats(flat, tuple_size)
            per_frame.append({
                "frame": frame,
                "min": mins,
                "max": maxs,
                "mean": means,
                "count": count,
            })
    finally:
        hou.setFrame(original_frame)

    return {
        "node_path": node_path,
        "attrib": attrib_name,
        "class": attrib_class,
        "components": components,
        "per_frame": per_frame,
    }

register_handler("geometry.attribute_stats", _attribute_stats)


###### geometry.compare_frames

def _compare_frames(
    *,
    node_path: str,
    frame_a: float,
    frame_b: float,
    attrib_name: str = "P",
    attrib_class: str = "point",
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Compare an attribute between two frames elementwise.

    Cooks the node at ``frame_a`` and ``frame_b``, reads the attribute at each,
    and reports the largest absolute difference. The canonical loop-seam check:
    e.g. for a 150-frame loop, frame 1 vs frame 151 should be identical.

    Args:
        node_path: SOP node path.
        frame_a: First frame.
        frame_b: Second frame.
        attrib_name: Attribute name (default "P").
        attrib_class: "point", "prim", "vertex", or "detail".
        tolerance: Max abs diff still treated as identical.

    Raises:
        ValueError: if the attribute or class is invalid.
    """
    original_frame = hou.frame()
    try:
        hou.setFrame(frame_a)
        geo_a = _get_sop_geo(node_path)
        flat_a, _, _ = _read_flat_attrib(geo_a, attrib_name, attrib_class)

        hou.setFrame(frame_b)
        geo_b = _get_sop_geo(node_path)
        flat_b, tuple_size, _ = _read_flat_attrib(
            geo_b, attrib_name, attrib_class
        )
    finally:
        hou.setFrame(original_frame)

    max_diff, num_differing, num_compared = _max_abs_diff(flat_a, flat_b)
    num_elements = num_compared // tuple_size if tuple_size else 0

    return {
        "node_path": node_path,
        "attrib": attrib_name,
        "frame_a": frame_a,
        "frame_b": frame_b,
        "identical": max_diff <= tolerance,
        "max_abs_diff": max_diff,
        "num_differing": num_differing,
        "num_elements": num_elements,
    }

register_handler("geometry.compare_frames", _compare_frames)


###### geometry.verify_animation

def _verify_animation(
    *,
    node_path: str,
    frames: list[float] | None = None,
    attrib_name: str = "P",
    attrib_class: str = "point",
) -> dict[str, Any]:
    """Check whether an attribute changes over the playback range.

    Samples several frames (the playback range when ``frames`` is omitted) and,
    for each, reports the max absolute difference of the attribute against the
    first sampled frame. ``is_animated`` is True when any frame differs.

    Args:
        node_path: SOP node path.
        frames: Frame numbers to sample; defaults to ~5 spread across the
            playback range.
        attrib_name: Attribute name (default "P").
        attrib_class: "point", "prim", "vertex", or "detail".

    Raises:
        ValueError: if the attribute or class is invalid.
    """
    if frames:
        sample_frames = list(frames)
    else:
        start, end = hou.playbar.playbackRange()
        if end <= start:
            sample_frames = [start]
        else:
            steps = 5
            sample_frames = [
                start + (end - start) * i / (steps - 1) for i in range(steps)
            ]

    max_change = 0.0
    per_frame_change: list[dict[str, Any]] = []
    original_frame = hou.frame()
    try:
        reference: list[float] | None = None
        for frame in sample_frames:
            hou.setFrame(frame)
            geo = _get_sop_geo(node_path)
            flat, _, _ = _read_flat_attrib(geo, attrib_name, attrib_class)
            if reference is None:
                reference = flat
                max_diff = 0.0
            else:
                max_diff, _, _ = _max_abs_diff(reference, flat)
            if max_diff > max_change:
                max_change = max_diff
            per_frame_change.append({"frame": frame, "max_diff": max_diff})
    finally:
        hou.setFrame(original_frame)

    return {
        "node_path": node_path,
        "attrib": attrib_name,
        "frames_checked": sample_frames,
        "is_animated": max_change > 1e-6,
        "max_change": max_change,
        "per_frame_change": per_frame_change,
    }

register_handler("geometry.verify_animation", _verify_animation)
