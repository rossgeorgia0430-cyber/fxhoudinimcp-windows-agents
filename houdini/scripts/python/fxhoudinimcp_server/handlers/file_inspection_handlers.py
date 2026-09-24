"""Inspect exported or external scene files: FBX round-trips and other HIP files.

``inspect_fbx`` imports the file into a temporary object subnet of the live
session and deletes it before returning (the scene is left marked modified).
``inspect_hip_file`` reads another HIP in a separate hython process, so the
interactive scene is untouched.
"""

from __future__ import annotations

# Built-in
import json
import os
import re
import subprocess
import tempfile
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_handler
from fxhoudinimcp_server.handlers.alembic_diagnostics_handlers import _node_or_raise
from fxhoudinimcp_server.handlers.rendering_handlers import _hython_path, _worker_script_path

_TRANSFORM_PARMS = ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz")
# FBX import replaces characters Houdini node names cannot hold.
_INVALID_NODE_NAME_CHARS = re.compile(r"[^0-9A-Za-z_]")
_HIP_INSPECT_TIMEOUT_SECONDS = 600


###### diagnostics.inspect_fbx

def _key_frames(node: hou.ObjNode) -> list[float]:
    frames = []
    for parm_name in _TRANSFORM_PARMS:
        parm = node.parm(parm_name)
        if parm is not None:
            frames.extend(key.frame() for key in parm.keyframes())
    return frames


def _is_animated(node: hou.ObjNode, keyed: set[str]) -> bool:
    """True when the object or any object it is parented to has transform keys."""
    while node is not None:
        if node.path() in keyed:
            return True
        inputs = node.inputs()
        node = inputs[0] if inputs else None
    return False


def _polygon_counts(geo: hou.Geometry) -> tuple[int, int]:
    """Return (closed polygons, other primitives such as imported curves)."""
    closed = sum(
        1 for prim in geo.iterPrims() if prim.type() == hou.primType.Polygon and prim.isClosed()
    )
    return closed, len(geo.prims()) - closed


def _bbox_center(points: list[hou.Vector3]) -> hou.Vector3:
    return hou.Vector3([
        (min(point[axis] for point in points) + max(point[axis] for point in points)) * 0.5
        for axis in range(3)
    ])


def _containing_object_transform(node: hou.Node) -> hou.Matrix4:
    parent = node.parent()
    while parent is not None and not isinstance(parent, hou.ObjNode):
        parent = parent.parent()
    return parent.worldTransform() if parent is not None else hou.hmath.identityTransform()


def _sop_piece_points(geo: hou.Geometry, name_attrib: str) -> dict[str, list[int]]:
    """Point numbers per sanitized piece name of an unpacked SOP."""
    attrib = geo.findPrimAttrib(name_attrib)
    if attrib is None:
        raise hou.OperationFailed(f"Primitive attribute not found: {name_attrib}")
    members: dict[str, set[int]] = {}
    for prim in geo.iterPrims():
        if isinstance(prim, hou.PackedPrim):
            raise hou.OperationFailed(
                "compare_sop_path holds packed primitives; point it at the unpacked pieces"
            )
        name = _INVALID_NODE_NAME_CHARS.sub("_", str(prim.attribValue(attrib)))
        members.setdefault(name, set()).update(point.number() for point in prim.points())
    return {name: sorted(points) for name, points in members.items()}


def _compare_to_sop(
    geo_nodes: dict[str, hou.ObjNode],
    sop: hou.Node,
    name_attrib: str,
    frames: list[float],
    max_listed: int,
) -> dict[str, Any]:
    """World bbox-centre error of each FBX mesh node against the same-named SOP piece."""
    sop_members = _sop_piece_points(sop.geometry(), name_attrib)
    matched = sorted(set(sop_members) & set(geo_nodes))
    per_frame = []
    for frame in frames:
        hou.setFrame(frame)
        flat = sop.geometry().pointFloatAttribValues("P")
        sop_matrix = _containing_object_transform(sop)
        worst_error, worst_name = 0.0, None
        for name in matched:
            reference = _bbox_center(
                [hou.Vector3(flat[3 * i:3 * i + 3]) * sop_matrix for i in sop_members[name]]
            )
            fbx_node = geo_nodes[name]
            fbx_matrix = fbx_node.worldTransform()
            exported = _bbox_center(
                [point.position() * fbx_matrix for point in fbx_node.displayNode().geometry().points()]
            )
            error = (exported - reference).length()
            if error > worst_error:
                worst_error, worst_name = error, name
        per_frame.append({"frame": frame, "max_error": round(worst_error, 6), "worst_piece": worst_name})
    return {
        "sop_path": sop.path(),
        "matched": len(matched),
        "sop_only": sorted(set(sop_members) - set(geo_nodes))[:max_listed],
        "fbx_only": sorted(set(geo_nodes) - set(sop_members))[:max_listed],
        "frames": per_frame,
        "max_error": max(row["max_error"] for row in per_frame) if per_frame else None,
    }


def inspect_fbx(
    file_path: str,
    compare_sop_path: str | None = None,
    name_attrib: str = "name",
    frames: list[float] | None = None,
    max_listed: int = 20,
) -> dict[str, Any]:
    """Re-import an FBX and report hierarchy, animation, materials, and split nodes."""
    path = hou.expandString(file_path)
    if not os.path.isfile(path):
        raise hou.OperationFailed(f"FBX file not found: {path}")
    sop = _node_or_raise(compare_sop_path) if compare_sop_path else None

    original_frame = hou.frame()
    subnet, messages = hou.hipFile.importFBX(path, suppress_save_prompt=True, convert_units=True)
    try:
        objects = [node for node in subnet.allSubChildren() if isinstance(node, hou.ObjNode)]
        keys_by_node = {node.path(): _key_frames(node) for node in objects}
        keyed = {node_path for node_path, keys in keys_by_node.items() if keys}
        all_keys = [frame for keys in keys_by_node.values() for frame in keys]
        # Bones without a mesh (such as the root) import as empty geo objects.
        geo_nodes = {
            node.name(): node
            for node in objects
            if node.type().name() == "geo" and node.displayNode() is not None
        }

        type_counts: dict[str, int] = {}
        for node in objects:
            type_counts[node.type().name()] = type_counts.get(node.type().name(), 0) + 1
        polygons = other_prims = 0
        curve_only = []
        for name, node in geo_nodes.items():
            closed, others = _polygon_counts(node.displayNode().geometry())
            polygons += closed
            other_prims += others
            if others and not closed:
                curve_only.append(name)
        static = [name for name, node in geo_nodes.items() if not _is_animated(node, keyed)] if keyed else []
        materials = sorted(
            child.name()
            for node in subnet.allSubChildren()
            if node.type().name() == "matnet"
            for child in node.children()
        )

        result: dict[str, Any] = {
            "file_path": path,
            "scene_fps": hou.fps(),
            "object_types": type_counts,
            "top_level": [child.name() for child in subnet.children()][:max_listed],
            "geo_nodes": len(geo_nodes),
            "polygons": polygons,
            "other_prims": other_prims,
            "key_range": [min(all_keys), max(all_keys)] if all_keys else None,
            "animated_objects": len(keyed),
            "static_geo_nodes": static[:max_listed],
            "static_geo_count": len(static),
            "curve_only_geo_nodes": curve_only[:max_listed],
            "materials": materials,
            "import_messages": messages.strip() or None,
        }
        if sop is not None:
            if frames is None:
                frames = (
                    [min(all_keys), round((min(all_keys) + max(all_keys)) * 0.5), max(all_keys)]
                    if all_keys else [original_frame]
                )
            result["comparison"] = _compare_to_sop(geo_nodes, sop, name_attrib, frames, max_listed)
        return result
    finally:
        subnet.destroy()
        hou.setFrame(original_frame)


register_handler("diagnostics.inspect_fbx", inspect_fbx)


###### diagnostics.inspect_hip_file

def inspect_hip_file(
    hip_path: str,
    root_path: str = "/obj",
    max_depth: int = 2,
    max_nodes: int = 100,
) -> dict[str, Any]:
    """Dump another HIP's nodes and non-default parameters from a separate hython."""
    path = hou.expandString(hip_path)
    if not os.path.isfile(path):
        raise hou.OperationFailed(f"HIP file not found: {path}")
    with tempfile.TemporaryDirectory() as work_dir:
        out_path = os.path.join(work_dir, "inspect.json")
        completed = subprocess.run(
            [
                _hython_path(),
                _worker_script_path("_hip_inspect_worker.py"),
                path,
                root_path,
                str(max_depth),
                str(max_nodes),
                out_path,
            ],
            capture_output=True,
            text=True,
            timeout=_HIP_INSPECT_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0 or not os.path.isfile(out_path):
            detail = (completed.stderr or completed.stdout).strip()[-2000:]
            raise hou.OperationFailed(f"hython inspection failed ({completed.returncode}): {detail}")
        with open(out_path, encoding="utf-8") as handle:
            return json.load(handle)


register_handler("diagnostics.inspect_hip_file", inspect_hip_file)
