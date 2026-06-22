"""COP (Copernicus) handlers for FXHoudini-MCP.

Provides tools for querying and manipulating COP nodes in Houdini 20.5+
Copernicus compositing networks.
"""

from __future__ import annotations

# Built-in
import logging

# Third-party
import hou

# Internal
from fxhoudinimcp_server.config import layout_if_enabled
from fxhoudinimcp_server.dispatcher import register_handler

logger = logging.getLogger(__name__)


###### Helpers

def _get_cop_node(node_path: str) -> hou.Node:
    """Return a COP node or raise if not found."""
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")
    return node


def _focus_network_editor(node: hou.Node) -> None:
    """Best-effort: layout the parent network, then pan the editor to *node*."""
    try:
        parent = node.parent()
        if parent is not None:
            layout_if_enabled(parent)
        for pane_tab in hou.ui.paneTabs():
            if pane_tab.type() == hou.paneTabType.NetworkEditor:
                if parent is not None:
                    pane_tab.cd(parent.path())
                pane_tab.setCurrentNode(node)
                pane_tab.homeToSelection()
                return
    except Exception:
        pass


def _require_cop(node_path: str, tool: str) -> tuple[hou.Node, str]:
    """Resolve a COP node and return (node, category), or raise a clear error.

    Accepts modern Copernicus ('Cop') and legacy 'Cop2' nodes.
    """
    node = _get_cop_node(node_path)
    category = node.type().category().name()
    if category not in ("Cop", "Cop2"):
        raise ValueError(
            f"'{node_path}' is a {category} node, not a COP. {tool} works on "
            "Copernicus ('Cop', Houdini 20.5+) or legacy 'Cop2' nodes."
        )
    return node, category


def _copernicus_image_info(node: hou.Node) -> tuple[list[int] | None, list[dict], str | None]:
    """Read a Copernicus node's output layers via the modern ImageLayer API.

    Returns (resolution [w, h] or None, per-layer detail list, error or None).
    Houdini 21 Copernicus nodes have no xRes()/planes() — image data is read
    from node.layer(output_index), each an ImageLayer with bufferResolution(),
    channelCount() and storageType().
    """
    try:
        names = list(node.outputNames())
    except Exception as e:  # noqa: BLE001 - report, don't mask
        return None, [], f"outputNames() failed: {type(e).__name__}: {e}"

    try:
        node.cook(force=False)
    except hou.OperationFailed as e:
        return None, [], f"cook failed: {e}"

    resolution: list[int] | None = None
    layers: list[dict] = []
    for index, name in enumerate(names):
        try:
            layer = node.layer(index)
            res = layer.bufferResolution()
            wh = [int(res[0]), int(res[1])]
            if resolution is None:
                resolution = wh
            layers.append(
                {
                    "name": name,
                    "output_index": index,
                    "resolution": wh,
                    "channels": int(layer.channelCount()),
                    "storage": str(layer.storageType()).rsplit(".", 1)[-1],
                }
            )
        except Exception as e:  # noqa: BLE001 - per-layer, keep going
            layers.append(
                {"name": name, "output_index": index, "error": f"{type(e).__name__}: {e}"}
            )
    return resolution, layers, None


###### cops.get_cop_info

def get_cop_info(node_path: str) -> dict:
    """Return information about a COP node.

    Includes output type, data format, and resolution info. Branches on the
    node category so modern Copernicus ('Cop') nodes report real resolution and
    layers via the ImageLayer API, while legacy 'Cop2' nodes use xRes/planes.

    Args:
        node_path: Path to the COP node.
    """
    node, category = _require_cop(node_path, "get_cop_info")

    info = {
        "node_path": node.path(),
        "node_type": node.type().name(),
        "category": category,
    }

    if category == "Cop":
        resolution, layers, err = _copernicus_image_info(node)
        info["x_resolution"] = resolution[0] if resolution else None
        info["y_resolution"] = resolution[1] if resolution else None
        info["layers"] = [layer_d["name"] for layer_d in layers]
        info["layer_details"] = layers
        if err:
            info["layer_read_error"] = err
    else:
        # Legacy COP2 image API.
        try:
            info["x_resolution"] = node.xRes()
            info["y_resolution"] = node.yRes()
        except (AttributeError, hou.OperationFailed):
            info["x_resolution"] = None
            info["y_resolution"] = None
        try:
            info["depth"] = str(node.depth())
        except (AttributeError, hou.OperationFailed):
            info["depth"] = None
        try:
            planes = node.planes()
            info["planes"] = list(planes) if planes else []
        except (AttributeError, hou.OperationFailed):
            info["planes"] = []
        try:
            info["sequence_start"] = node.sequenceStartFrame()
            info["sequence_end"] = node.sequenceEndFrame()
            info["sequence_length"] = node.sequenceFrameLength()
        except (AttributeError, hou.OperationFailed):
            info["sequence_start"] = None
            info["sequence_end"] = None
            info["sequence_length"] = None

    # Common: input/output counts and errors/warnings.
    try:
        info["num_inputs"] = len(node.inputs())
        info["num_outputs"] = len(node.outputs())
    except (hou.OperationFailed, hou.ObjectWasDeleted, AttributeError) as e:
        logger.debug("Could not read input/output counts for '%s': %s", node_path, e)
        info["num_inputs"] = 0
        info["num_outputs"] = 0

    try:
        info["errors"] = list(node.errors())
        info["warnings"] = list(node.warnings())
    except (hou.OperationFailed, hou.ObjectWasDeleted, AttributeError) as e:
        logger.debug("Could not read errors/warnings for '%s': %s", node_path, e)
        info["errors"] = []
        info["warnings"] = []

    return info


###### cops.get_cop_geometry

def get_cop_geometry(node_path: str, output_index: int = 0) -> dict:
    """Get geometry representation from a COP node.

    In Copernicus (Houdini 20.5+), COP nodes can output geometry data.

    Args:
        node_path: Path to the COP node.
        output_index: Output connector index (default 0).
    """
    node = _get_cop_node(node_path)

    try:
        geo = node.geometry(output_index)
    except Exception as e:
        raise ValueError(
            f"Failed to get geometry from COP node {node_path} "
            f"at output {output_index}: {e}"
        )

    if geo is None:
        return {
            "node_path": node.path(),
            "output_index": output_index,
            "has_geometry": False,
            "message": "No geometry data at this output.",
        }

    # Gather geometry info
    result = {
        "node_path": node.path(),
        "output_index": output_index,
        "has_geometry": True,
        "num_points": geo.intrinsicValue("pointcount") if geo.intrinsicValue("pointcount") is not None else len(geo.points()),
        "num_prims": geo.intrinsicValue("primitivecount") if geo.intrinsicValue("primitivecount") is not None else len(geo.prims()),
        "num_vertices": geo.intrinsicValue("vertexcount") if geo.intrinsicValue("vertexcount") is not None else len(geo.vertices()),
    }

    # Point attributes
    try:
        point_attribs = []
        for attrib in geo.pointAttribs():
            point_attribs.append({
                "name": attrib.name(),
                "type": str(attrib.dataType()),
                "size": attrib.size(),
            })
        result["point_attributes"] = point_attribs
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read point attributes for '%s': %s", node_path, e)
        result["point_attributes"] = []

    # Primitive attributes
    try:
        prim_attribs = []
        for attrib in geo.primAttribs():
            prim_attribs.append({
                "name": attrib.name(),
                "type": str(attrib.dataType()),
                "size": attrib.size(),
            })
        result["primitive_attributes"] = prim_attribs
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read primitive attributes for '%s': %s", node_path, e)
        result["primitive_attributes"] = []

    # Bounding box
    try:
        bbox = geo.boundingBox()
        result["bounding_box"] = {
            "min": list(bbox.minvec()),
            "max": list(bbox.maxvec()),
        }
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not read bounding box for '%s': %s", node_path, e)
        result["bounding_box"] = None

    return result


###### cops.get_cop_layer

def get_cop_layer(node_path: str, output_index: int = 0) -> dict:
    """Get image layer data information from a COP node.

    For Copernicus ('Cop') nodes this returns every output layer with its real
    resolution, channel count and storage type (read from the ImageLayer API);
    for legacy 'Cop2' nodes it falls back to planes/components.

    Args:
        node_path: Path to the COP node.
        output_index: Ignored for Copernicus (all layers are returned); used by
            legacy Cop2 resolution lookups.
    """
    node, category = _require_cop(node_path, "get_cop_layer")

    result: dict = {
        "node_path": node.path(),
        "category": category,
    }

    if category == "Cop":
        resolution, layers, err = _copernicus_image_info(node)
        result["layers"] = layers
        result["layer_count"] = len(layers)
        result["x_resolution"] = resolution[0] if resolution else None
        result["y_resolution"] = resolution[1] if resolution else None
        if err:
            result["layer_read_error"] = err
        return result

    # Legacy COP2: planes/components.
    result["output_index"] = output_index
    try:
        planes = node.planes()
        layer_info = []
        for plane in planes:
            components = node.components(plane)
            layer_info.append(
                {
                    "plane_name": plane,
                    "components": list(components) if components else [],
                    "depth": str(node.planeDepth(plane)) if hasattr(node, "planeDepth") else None,
                }
            )
        result["layers"] = layer_info
        result["layer_count"] = len(layer_info)
    except (AttributeError, hou.OperationFailed):
        result["layers"] = []
        result["layer_count"] = 0

    try:
        result["x_resolution"] = node.xRes()
        result["y_resolution"] = node.yRes()
    except (AttributeError, hou.OperationFailed):
        result["x_resolution"] = None
        result["y_resolution"] = None

    return result


###### cops.create_cop_node

def create_cop_node(
    parent_path: str,
    cop_type: str,
    name: str = None,
) -> dict:
    """Create a Copernicus COP node.

    Args:
        parent_path: Path to the parent COP network.
        cop_type: The COP node type to create (e.g. "file", "vopcop2gen").
        name: Optional explicit name for the node.
    """
    parent = hou.node(parent_path)
    if parent is None:
        raise ValueError(f"Parent node not found: {parent_path}")

    try:
        if name:
            node = parent.createNode(cop_type, name)
        else:
            node = parent.createNode(cop_type)
    except hou.OperationFailed as e:
        raise ValueError(
            f"Failed to create COP node of type '{cop_type}' "
            f"under {parent_path}: {e}"
        )

    _focus_network_editor(node)

    return {
        "success": True,
        "node_path": node.path(),
        "node_type": node.type().name(),
        "node_name": node.name(),
    }


###### cops.set_cop_flags

def set_cop_flags(
    node_path: str,
    display: bool = None,
    export_flag: bool = None,
    compress: bool = None,
) -> dict:
    """Set flags on a COP node.

    Args:
        node_path: Path to the COP node.
        display: Set the display flag.
        export_flag: Set the render/export flag.
        compress: Set the compress flag.
    """
    node = _get_cop_node(node_path)

    flags_set = {}

    if display is not None:
        try:
            node.setDisplayFlag(display)
            flags_set["display"] = display
        except Exception as e:
            flags_set["display_error"] = str(e)

    if export_flag is not None:
        try:
            node.setRenderFlag(export_flag)
            flags_set["export_flag"] = export_flag
        except Exception as e:
            flags_set["export_flag_error"] = str(e)

    if compress is not None:
        try:
            node.setCompressFlag(compress)
            flags_set["compress"] = compress
        except (AttributeError, Exception) as e:
            flags_set["compress_error"] = str(e)

    return {
        "success": True,
        "node_path": node.path(),
        "flags_set": flags_set,
    }


###### cops.list_cop_node_types

def list_cop_node_types(filter: str = None) -> dict:
    """List available COP node types.

    Args:
        filter: Optional substring filter for node type names.
    """
    # Copernicus ("Cop", Houdini 20.5+) is what create_cop_node creates
    # inside a copnet; fall back to legacy Cop2 on older builds.
    cop_types = []

    try:
        category = hou.nodeTypeCategories().get("Cop") or hou.cop2NodeTypeCategory()
        for type_name, node_type in category.nodeTypes().items():
            if filter and filter.lower() not in type_name.lower():
                continue
            type_info = {
                "name": type_name,
                "label": node_type.description(),
            }
            try:
                type_info["icon"] = node_type.icon()
            except (hou.OperationFailed, AttributeError) as e:
                logger.debug("Could not read icon for COP type '%s': %s", type_name, e)
                type_info["icon"] = None
            try:
                type_info["min_inputs"] = node_type.minNumInputs()
                type_info["max_inputs"] = node_type.maxNumInputs()
            except (hou.OperationFailed, AttributeError) as e:
                logger.debug("Could not read input counts for COP type '%s': %s", type_name, e)
                type_info["min_inputs"] = None
                type_info["max_inputs"] = None
            cop_types.append(type_info)
    except (hou.OperationFailed, AttributeError) as e:
        logger.debug("Could not enumerate COP2 node types: %s", e)

    # Sort by name
    cop_types.sort(key=lambda x: x["name"])

    return {
        "cop_type_count": len(cop_types),
        "cop_types": cop_types,
        "filter_applied": filter,
    }


###### cops.get_cop_vdb

def get_cop_vdb(node_path: str, output_index: int = 0) -> dict:
    """Get VDB data information from a COP node.

    In Copernicus, COP nodes can work with volumetric (VDB) data.

    Args:
        node_path: Path to the COP node.
        output_index: Output connector index (default 0).
    """
    node = _get_cop_node(node_path)

    try:
        geo = node.geometry(output_index)
    except Exception as e:
        raise ValueError(
            f"Failed to get geometry from COP node {node_path} "
            f"at output {output_index}: {e}"
        )

    if geo is None:
        return {
            "node_path": node.path(),
            "output_index": output_index,
            "has_vdb": False,
            "message": "No geometry data at this output.",
        }

    # Look for VDB primitives
    vdb_prims = []
    try:
        for prim in geo.prims():
            if prim.type() == hou.primType.VDB:
                vdb_info = {
                    "name": prim.attribValue("name") if prim.attribValue("name") else f"vdb_{prim.number()}",
                    "prim_index": prim.number(),
                    "type": str(prim.type()),
                }
                try:
                    vdb_info["data_type"] = str(prim.dataType())
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read VDB data_type: %s", e)
                    vdb_info["data_type"] = None
                try:
                    vdb_info["voxel_count"] = prim.voxelCount()
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read VDB voxel_count: %s", e)
                    vdb_info["voxel_count"] = None
                try:
                    vdb_info["active_voxel_count"] = prim.activeVoxelCount()
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read VDB active_voxel_count: %s", e)
                    vdb_info["active_voxel_count"] = None
                try:
                    bbox = prim.boundingBox()
                    vdb_info["bounding_box"] = {
                        "min": list(bbox.minvec()),
                        "max": list(bbox.maxvec()),
                    }
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read VDB bounding box: %s", e)
                    vdb_info["bounding_box"] = None
                try:
                    vdb_info["transform"] = [list(row) for row in prim.transform().asTupleOfTuples()]
                except (hou.OperationFailed, AttributeError) as e:
                    logger.debug("Could not read VDB transform: %s", e)
                    vdb_info["transform"] = None

                vdb_prims.append(vdb_info)
    except Exception as e:
        return {
            "node_path": node.path(),
            "output_index": output_index,
            "has_vdb": False,
            "error": str(e),
        }

    return {
        "node_path": node.path(),
        "output_index": output_index,
        "has_vdb": len(vdb_prims) > 0,
        "vdb_count": len(vdb_prims),
        "vdb_primitives": vdb_prims,
    }


###### Registration

register_handler("cops.get_cop_info", get_cop_info)
register_handler("cops.get_cop_geometry", get_cop_geometry)
register_handler("cops.get_cop_layer", get_cop_layer)
register_handler("cops.create_cop_node", create_cop_node)
register_handler("cops.set_cop_flags", set_cop_flags)
register_handler("cops.list_cop_node_types", list_cop_node_types)
register_handler("cops.get_cop_vdb", get_cop_vdb)
