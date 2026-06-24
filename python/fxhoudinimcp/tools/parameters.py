"""MCP tools for Houdini parameter operations.

Exposes 12 tools covering parameter get/set, expressions, channel
references, locking, schema inspection, and spare parameter creation.
"""

from __future__ import annotations

# Built-in
from typing import Any

# Third-party
from mcp.server.fastmcp import Context

# Internal
from fxhoudinimcp._specs import SpareParameterSpec
from fxhoudinimcp._types import Value
from fxhoudinimcp.server import mcp, _get_bridge


###### parameters.get_parameter


@mcp.tool()
async def get_parameter(ctx: Context, node_path: str, parm_name: str) -> dict:
    """Get the value and metadata of a parameter.

    Args:
        node_path: Node path.
        parm_name: Parameter name.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.get_parameter",
        {"node_path": node_path, "parm_name": parm_name},
    )


###### parameters.set_parameter


@mcp.tool()
async def set_parameter(
    ctx: Context, node_path: str, parm_name: str, value: Value
) -> dict:
    """Set a parameter value.

    Args:
        node_path: Node path.
        parm_name: Parameter name.
        value: New value (int, float, string, bool, or list).
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.set_parameter",
        {"node_path": node_path, "parm_name": parm_name, "value": value},
    )


###### parameters.set_parameters


@mcp.tool()
async def set_parameters(
    ctx: Context, node_path: str, params: dict[str, Value], atomic: bool = True
) -> dict:
    """Batch-set multiple parameters on a node.

    For multi-component parms pass a list against the tuple name, e.g.
    {"t": [0, 1, 0], "size": [2, 2, 2]} — a scalar against a tuple name is
    broadcast across components. The result reports `applied`, the read-back
    `set` values, and a per-item `errors` list.

    Args:
        node_path: Node path.
        params: Mapping of parameter names to values.
        atomic: When true (default), change nothing unless every parameter
            resolves and applies — a failure rolls the batch back so you never
            get a half-applied node. Set false to apply whatever succeeds.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.set_parameters",
        {"node_path": node_path, "params": params, "atomic": atomic},
    )


###### parameters.get_parameter_schema


@mcp.tool()
async def get_parameter_schema(
    ctx: Context,
    node_path: str,
    parm_name: str | None = None,
    filter: str | None = None,
) -> dict:
    """Get the template schema for parameter(s) on a node.

    Most nodes have dozens of parameters; many have 100+. Always use
    `parm_name` or `filter` unless you genuinely need the full list.

    Args:
        node_path: Node path.
        parm_name: Exact parameter name for a single-parameter lookup.
        filter: Substring to match against parameter name or label
                (case-insensitive). Use instead of dumping all params.
    """
    bridge = _get_bridge(ctx)
    payload: dict[str, Any] = {"node_path": node_path}
    if parm_name is not None:
        payload["parm_name"] = parm_name
    if filter is not None:
        payload["filter"] = filter
    return await bridge.execute("parameters.get_parameter_schema", payload)


###### parameters.set_expression


@mcp.tool()
async def set_expression(
    ctx: Context,
    node_path: str,
    parm_name: str,
    expression: str,
    language: str = "hscript",
) -> dict:
    """Set an expression on a parameter.

    Args:
        node_path: Node path.
        parm_name: Parameter name.
        expression: Expression string.
        language: "hscript" (default) or "python".
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.set_expression",
        {
            "node_path": node_path,
            "parm_name": parm_name,
            "expression": expression,
            "language": language,
        },
    )


###### parameters.get_expression


@mcp.tool()
async def get_expression(ctx: Context, node_path: str, parm_name: str) -> dict:
    """Get the expression on a parameter.

    Args:
        node_path: Node path.
        parm_name: Parameter name.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.get_expression",
        {"node_path": node_path, "parm_name": parm_name},
    )


###### parameters.press_button


@mcp.tool()
async def press_button(
    ctx: Context,
    node_path: str,
    parm_name: str,
    timeout: float | None = None,
) -> dict:
    """Trigger a button (callback) parameter on a node.

    A button callback runs synchronously and can take a long time — an HDA
    "Render All" or "Reload Geometry" may cook for many seconds. Pass an
    explicit `timeout` for such buttons so the default (120s) doesn't abandon
    the call mid-callback.

    Args:
        node_path: Node path.
        parm_name: Button parameter name.
        timeout: Operation budget in seconds. Omit for the default (120s).
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.press_button",
        {"node_path": node_path, "parm_name": parm_name},
        timeout=timeout,
    )


###### parameters.revert_parameter


@mcp.tool()
async def revert_parameter(
    ctx: Context, node_path: str, parm_name: str
) -> dict:
    """Revert a parameter to its default value.

    Args:
        node_path: Node path.
        parm_name: Parameter name.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.revert_parameter",
        {"node_path": node_path, "parm_name": parm_name},
    )


###### parameters.link_parameters


@mcp.tool()
async def link_parameters(
    ctx: Context,
    source_path: str,
    source_parm: str,
    dest_path: str,
    dest_parm: str,
) -> dict:
    """Create a channel reference from one parameter to another.

    Args:
        source_path: Source node path.
        source_parm: Source parameter name.
        dest_path: Destination node path.
        dest_parm: Destination parameter name.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.link_parameters",
        {
            "source_path": source_path,
            "source_parm": source_parm,
            "dest_path": dest_path,
            "dest_parm": dest_parm,
        },
    )


###### parameters.lock_parameter


@mcp.tool()
async def lock_parameter(
    ctx: Context, node_path: str, parm_name: str, locked: bool
) -> dict:
    """Lock or unlock a parameter.

    Args:
        node_path: Node path.
        parm_name: Parameter name.
        locked: True to lock, False to unlock.
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "parameters.lock_parameter",
        {"node_path": node_path, "parm_name": parm_name, "locked": locked},
    )


###### parameters.create_spare_parameter


@mcp.tool()
async def create_spare_parameter(
    ctx: Context,
    node_path: str,
    parm_name: str,
    parm_type: str,
    label: str,
    default_value: Value | None = None,
    min_val: float | None = None,
    max_val: float | None = None,
) -> dict:
    """Add a spare parameter to a node.

    Args:
        node_path: Node path.
        parm_name: Internal parameter name.
        parm_type: "float", "int", "string", "toggle", or "menu".
        label: UI label.
        default_value: Default value.
        min_val: Minimum value (float/int only).
        max_val: Maximum value (float/int only).
    """
    bridge = _get_bridge(ctx)
    payload: dict[str, Any] = {
        "node_path": node_path,
        "parm_name": parm_name,
        "parm_type": parm_type,
        "label": label,
    }
    if default_value is not None:
        payload["default_value"] = default_value
    if min_val is not None:
        payload["min_val"] = min_val
    if max_val is not None:
        payload["max_val"] = max_val
    return await bridge.execute("parameters.create_spare_parameter", payload)


@mcp.tool()
async def create_spare_parameters(
    ctx: Context,
    node_path: str,
    parameters: list[SpareParameterSpec],
    folder_name: str | None = None,
    folder_type: str = "Tabs",
) -> dict:
    """Batch-create multiple spare parameters in one call, optionally in a folder tab.

    Args:
        node_path: Node path.
        parameters: List of parameter specs. Each dict has keys:
            parm_name (str), parm_type (str: "float"/"int"/"string"/"toggle"/"menu"),
            label (str), default_value (optional), min_val (optional), max_val (optional).
        folder_name: If provided, wraps all parameters in a named folder tab.
        folder_type: Folder style: "Tabs", "Collapsible", or "Simple".
    """
    bridge = _get_bridge(ctx)
    payload: dict[str, Any] = {
        "node_path": node_path,
        "parameters": [
            parameter.model_dump(exclude_none=True)
            if isinstance(parameter, SpareParameterSpec)
            else parameter
            for parameter in parameters
        ],
    }
    if folder_name is not None:
        payload["folder_name"] = folder_name
        payload["folder_type"] = folder_type
    return await bridge.execute("parameters.create_spare_parameters", payload)
