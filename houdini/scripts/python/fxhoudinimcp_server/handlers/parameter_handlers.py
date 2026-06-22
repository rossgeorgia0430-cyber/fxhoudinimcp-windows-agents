"""Houdini-side handlers for parameter operations.

Provides 10 command handlers for reading, writing, and managing
node parameters, expressions, channel references, and spare parameters.
"""

from __future__ import annotations

# Built-in
from difflib import get_close_matches
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_handler


###### Helpers


def _resolve_node(node_path: str) -> hou.Node:
    """Return the hou.Node at *node_path* or raise."""
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")
    return node


def _available_parm_names(node: hou.Node) -> list[str]:
    """Return sorted list of parameter names on a node."""
    return sorted(p.name() for p in node.parms())


def _resolve_parm(node_path: str, parm_name: str) -> hou.Parm:
    """Return the hou.Parm on *node_path* named *parm_name* or raise."""
    node = _resolve_node(node_path)
    parm = node.parm(parm_name)
    if parm is None:
        available = _available_parm_names(node)
        close = get_close_matches(parm_name, available, n=3, cutoff=0.4)
        hint = f" Did you mean: {close}?" if close else ""
        raise ValueError(
            f"Parameter '{parm_name}' not found on node '{node_path}'.{hint} "
            f"Available parameters: {available}"
        )
    return parm


def _parm_type_name(parm_template: hou.ParmTemplate) -> str:
    """Return a human-readable type string for a parameter template."""
    return parm_template.type().name()


def _serialize_value(value: Any) -> Any:
    """Convert a value to a JSON-safe Python type."""
    if isinstance(value, hou.Vector2):
        return list(value)
    if isinstance(value, hou.Vector3):
        return list(value)
    if isinstance(value, hou.Vector4):
        return list(value)
    if isinstance(value, hou.Matrix3):
        return [list(row) for row in value.asTupleOfTuples()]
    if isinstance(value, hou.Matrix4):
        return [list(row) for row in value.asTupleOfTuples()]
    if isinstance(value, hou.Ramp):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    return value


def _snapshot_parm_state(parm: hou.Parm) -> dict[str, Any]:
    """Capture a parm in a form that preserves animation and references.

    ``parm.eval()`` alone is not a reversible snapshot: restoring that value
    would replace a channel reference, expression, or keyframe curve with a
    constant.  Batch mutation needs to preserve those authored states when a
    later item fails.
    """
    expression: str | None = None
    expression_language = None
    try:
        expression = parm.expression()
        expression_language = parm.expressionLanguage()
    except hou.OperationFailed:
        pass

    return {
        "parm": parm,
        "value": parm.eval(),
        "expression": expression,
        "expression_language": expression_language,
        "keyframes": list(parm.keyframes()),
    }


def _snapshot_target(target: Any, is_tuple: bool) -> list[dict[str, Any]]:
    """Snapshot every component touched by a parameter or parm tuple."""
    if is_tuple:
        return [_snapshot_parm_state(parm) for parm in target]
    return [_snapshot_parm_state(target)]


def _restore_parm_state(state: dict[str, Any]) -> None:
    """Restore one parameter without flattening expressions or keyframes."""
    parm: hou.Parm = state["parm"]
    keyframes = state["keyframes"]
    expression = state["expression"]

    # Clear the temporary value/key made by the failed batch before restoring
    # the exact authored control source.
    parm.deleteAllKeyframes()
    if keyframes:
        parm.setKeyframes(keyframes)
    elif expression is not None:
        parm.setExpression(expression, state["expression_language"])
    else:
        parm.set(state["value"])


def _template_to_dict(pt: hou.ParmTemplate) -> dict[str, Any]:
    """Convert a ParmTemplate to a JSON-serialisable dictionary."""
    info: dict[str, Any] = {
        "name": pt.name(),
        "label": pt.label(),
        "type": _parm_type_name(pt),
        "num_components": pt.numComponents(),
        "is_hidden": pt.isHidden(),
    }

    # Default value
    try:
        info["default_value"] = list(pt.defaultValue())
    except Exception:
        try:
            info["default_value"] = pt.defaultValue()
        except Exception:
            info["default_value"] = None

    # Range
    try:
        info["min"] = pt.minValue()
        info["max"] = pt.maxValue()
        info["min_is_strict"] = pt.minIsStrict()
        info["max_is_strict"] = pt.maxIsStrict()
    except Exception:
        pass

    # Menu items — capped at 50 to avoid enormous enum lists
    try:
        items = pt.menuItems()
        labels = pt.menuLabels()
        if items:
            info["menu_items"] = list(items)[:50]
            info["menu_labels"] = list(labels)[:50]
            if len(items) > 50:
                info["menu_items_truncated"] = True
    except Exception:
        pass

    # Naming scheme (for multi-component parms)
    try:
        info["naming_scheme"] = pt.namingScheme().name()
    except Exception:
        pass

    # Conditionals and tags omitted — internal Houdini UI metadata,
    # not useful for LLM-driven parameter setting.

    return info


###### Handler: parameters.get_parameter


def _get_parameter(node_path: str, parm_name: str, **_: Any) -> dict[str, Any]:
    """Get the current value, expression, keyframe info, and metadata of a parameter."""
    parm = _resolve_parm(node_path, parm_name)
    pt = parm.parmTemplate()

    result: dict[str, Any] = {
        "node_path": node_path,
        "parm_name": parm_name,
        "value": _serialize_value(parm.eval()),
        "raw_value": _serialize_value(parm.rawValue()),
        "parm_type": _parm_type_name(pt),
        "is_locked": parm.isLocked(),
        "is_at_default": parm.isAtDefault(),
    }

    # Expression
    try:
        result["expression"] = parm.expression()
        result["expression_language"] = parm.expressionLanguage().name()
    except hou.OperationFailed:
        result["expression"] = None
        result["expression_language"] = None

    # Keyframes
    keyframes = parm.keyframes()
    result["keyframe_count"] = len(keyframes)

    return result


register_handler("parameters.get_parameter", _get_parameter)


###### Handler: parameters.set_parameter


def _set_parameter(
    node_path: str, parm_name: str, value: Any, **_: Any
) -> dict[str, Any]:
    """Set a parameter value, auto-detecting the appropriate type.

    A list/tuple value addressed at a vector parameter name (e.g. "size"
    on a box, "t" on a transform) is applied to the whole parm tuple, so
    callers are not forced to know the per-component names (sizex, ...).
    """
    if isinstance(value, (list, tuple)):
        node = _resolve_node(node_path)
        parm_tuple = node.parmTuple(parm_name)
        if parm_tuple is not None:
            if len(value) != len(parm_tuple):
                raise ValueError(
                    f"Parameter '{parm_name}' on {node_path} has "
                    f"{len(parm_tuple)} components, got {len(value)} values."
                )
            parm_tuple.set(value)
            return {
                "node_path": node_path,
                "parm_name": parm_name,
                "new_value": [_serialize_value(p.eval()) for p in parm_tuple],
            }

    parm = _resolve_parm(node_path, parm_name)

    parm.set(value)

    return {
        "node_path": node_path,
        "parm_name": parm_name,
        "new_value": _serialize_value(parm.eval()),
    }


register_handler("parameters.set_parameter", _set_parameter)


###### Handler: parameters.set_parameters


def _set_parameters(
    node_path: str, params: dict[str, Any], atomic: bool = True, **_: Any
) -> dict[str, Any]:
    """Batch-set multiple parameters on a single node.

    Mirrors set_parameter's value handling: a list/tuple value targets the
    whole parm tuple (so {"size": [1, 2, 3]} works, not just the per-component
    names), and a scalar aimed at a tuple name is broadcast across components.

    When ``atomic`` is True (default) the call first resolves every target and
    changes nothing if any name is unresolved or any tuple arity is wrong; if a
    later ``.set()`` still raises, the already-applied values are rolled back.
    This guarantees the agent never lands a half-applied batch it can't see.
    """
    node = _resolve_node(node_path)
    available = _available_parm_names(node)

    # Phase 1: resolve every target up front (no mutation yet).
    planned: list[tuple[str, Any, Any, bool]] = []  # name, target, value, is_tuple
    errors: list[dict[str, str]] = []
    for name, value in params.items():
        if isinstance(value, (list, tuple)):
            parm_tuple = node.parmTuple(name)
            if parm_tuple is None:
                close = get_close_matches(name, available, n=3, cutoff=0.4)
                hint = f" Did you mean: {close}?" if close else ""
                errors.append(
                    {"parm_name": name, "error": f"Parameter tuple '{name}' not found.{hint}"}
                )
                continue
            if len(value) != len(parm_tuple):
                errors.append(
                    {
                        "parm_name": name,
                        "error": (
                            f"'{name}' has {len(parm_tuple)} components, "
                            f"got {len(value)} values."
                        ),
                    }
                )
                continue
            planned.append((name, parm_tuple, list(value), True))
            continue

        parm = node.parm(name)
        if parm is not None:
            planned.append((name, parm, value, False))
            continue
        # A scalar aimed at a tuple name (e.g. "t" -> tx/ty/tz): broadcast.
        parm_tuple = node.parmTuple(name)
        if parm_tuple is not None:
            planned.append((name, parm_tuple, [value] * len(parm_tuple), True))
            continue
        close = get_close_matches(name, available, n=3, cutoff=0.4)
        hint = f" Did you mean: {close}?" if close else ""
        errors.append(
            {"parm_name": name, "error": f"Parameter '{name}' not found.{hint}"}
        )

    if atomic and errors:
        return {
            "node_path": node_path,
            "applied": False,
            "set": [],
            "errors": errors,
            "note": "atomic: nothing changed because some parameters could not be resolved",
        }

    # Phase 2: apply, snapshotting authored state so a mid-batch failure can
    # restore expressions, channel references, and keyframe curves exactly.
    applied: list[list[dict[str, Any]]] = []
    results: list[dict[str, Any]] = []
    for name, target, value, is_tuple in planned:
        try:
            snapshot = _snapshot_target(target, is_tuple)
            target.set(value)
            applied.append(snapshot)
            new_value = (
                [_serialize_value(p.eval()) for p in target]
                if is_tuple
                else _serialize_value(target.eval())
            )
            results.append({"parm_name": name, "new_value": new_value})
        except Exception as exc:
            if atomic:
                rollback_errors: list[str] = []
                for snapshot in reversed(applied):
                    for state in reversed(snapshot):
                        try:
                            _restore_parm_state(state)
                        except Exception as rollback_exc:
                            rollback_errors.append(str(rollback_exc))
                note = (
                    "atomic: batch failed and every applied parameter was restored"
                    if not rollback_errors
                    else "atomic: batch failed, but one or more rollback operations failed"
                )
                return {
                    "node_path": node_path,
                    "applied": False,
                    "set": [] if not rollback_errors else results,
                    "errors": errors + [{"parm_name": name, "error": f"rolled back: {exc}"}],
                    "rollback_errors": rollback_errors,
                    "note": note,
                }
            errors.append({"parm_name": name, "error": str(exc)})

    return {
        "node_path": node_path,
        "applied": bool(results),
        "set": results,
        "errors": errors,
    }


register_handler("parameters.set_parameters", _set_parameters)


###### Handler: parameters.get_parameter_schema


def _get_parameter_schema(
    node_path: str,
    parm_name: str | None = None,
    filter: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Get full parameter template info.

    If *parm_name* is given, return info for that one parameter.
    If *filter* is given, return only parameters whose name or label
    contains the filter string (case-insensitive).
    Otherwise return all non-hidden parameters.
    """
    node = _resolve_node(node_path)

    if parm_name is not None:
        parm = node.parm(parm_name)
        if parm is None:
            return {
                "node_path": node_path,
                "error": f"Parameter '{parm_name}' not found",
                "available_parameters": _available_parm_names(node),
            }
        return {
            "node_path": node_path,
            "parameter": _template_to_dict(parm.parmTemplate()),
        }

    # All parameters — hidden params skipped to keep the response compact.
    ptg = node.parmTemplateGroup()
    parm_infos: list[dict[str, Any]] = []

    def _walk(entries: tuple) -> None:
        for entry in entries:
            if isinstance(entry, hou.FolderParmTemplate):
                _walk(entry.parmTemplates())
            elif not entry.isHidden():
                parm_infos.append(_template_to_dict(entry))

    _walk(ptg.parmTemplates())

    if filter:
        f = filter.lower()
        parm_infos = [
            p for p in parm_infos
            if f in p["name"].lower() or f in p["label"].lower()
        ]

    return {
        "node_path": node_path,
        "parameter_count": len(parm_infos),
        "parameters": parm_infos,
    }


register_handler("parameters.get_parameter_schema", _get_parameter_schema)


###### Handler: parameters.set_expression


def _set_expression(
    node_path: str,
    parm_name: str,
    expression: str,
    language: str = "hscript",
    **_: Any,
) -> dict[str, Any]:
    """Set an expression on a parameter."""
    parm = _resolve_parm(node_path, parm_name)

    if language.lower() == "python":
        lang = hou.exprLanguage.Python
    else:
        lang = hou.exprLanguage.Hscript

    parm.setExpression(expression, lang)

    return {
        "node_path": node_path,
        "parm_name": parm_name,
        "expression": expression,
        "language": language,
    }


register_handler("parameters.set_expression", _set_expression)


###### Handler: parameters.get_expression


def _get_expression(node_path: str, parm_name: str, **_: Any) -> dict[str, Any]:
    """Get the current expression on a parameter."""
    parm = _resolve_parm(node_path, parm_name)

    try:
        expr = parm.expression()
        lang = parm.expressionLanguage().name()
    except hou.OperationFailed:
        expr = None
        lang = None

    return {
        "node_path": node_path,
        "parm_name": parm_name,
        "expression": expr,
        "language": lang,
    }


register_handler("parameters.get_expression", _get_expression)


###### Handler: parameters.revert_parameter


def _revert_parameter(
    node_path: str, parm_name: str, **_: Any
) -> dict[str, Any]:
    """Revert a parameter to its default value."""
    parm = _resolve_parm(node_path, parm_name)

    parm.revertToDefaults()

    return {
        "node_path": node_path,
        "parm_name": parm_name,
        "reverted": True,
        "value": _serialize_value(parm.eval()),
    }


register_handler("parameters.revert_parameter", _revert_parameter)


###### Handler: parameters.link_parameters


def _link_parameters(
    source_path: str,
    source_parm: str,
    dest_path: str,
    dest_parm: str,
    **_: Any,
) -> dict[str, Any]:
    """Create a channel reference from destination parameter to source parameter."""
    src = _resolve_parm(source_path, source_parm)
    dst = _resolve_parm(dest_path, dest_parm)

    # Build the channel reference expression
    ref_expr = 'ch("{}")'.format(src.path())
    dst.setExpression(ref_expr, hou.exprLanguage.Hscript)

    return {
        "source": src.path(),
        "destination": dst.path(),
        "expression": ref_expr,
    }


register_handler("parameters.link_parameters", _link_parameters)


###### Handler: parameters.lock_parameter


def _lock_parameter(
    node_path: str, parm_name: str, locked: bool, **_: Any
) -> dict[str, Any]:
    """Lock or unlock a parameter."""
    parm = _resolve_parm(node_path, parm_name)

    parm.lock(locked)

    return {
        "node_path": node_path,
        "parm_name": parm_name,
        "locked": parm.isLocked(),
    }


register_handler("parameters.lock_parameter", _lock_parameter)


###### Handler: parameters.create_spare_parameter


def _create_spare_parameter(
    node_path: str,
    parm_name: str,
    parm_type: str,
    label: str,
    default_value: Any = None,
    min_val: float | None = None,
    max_val: float | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Add a custom spare parameter to a node."""
    node = _resolve_node(node_path)

    # Map string type names to ParmTemplate constructors
    type_map: dict[str, type] = {
        "float": hou.FloatParmTemplate,
        "int": hou.IntParmTemplate,
        "string": hou.StringParmTemplate,
        "toggle": hou.ToggleParmTemplate,
        "menu": hou.MenuParmTemplate,
    }

    template_cls = type_map.get(parm_type.lower())
    if template_cls is None:
        raise ValueError(
            f"Unsupported parm_type '{parm_type}'. "
            f"Supported types: {list(type_map.keys())}"
        )

    # Build keyword arguments for the template constructor
    kwargs: dict[str, Any] = {}

    if template_cls in (hou.FloatParmTemplate, hou.IntParmTemplate):
        # Cast to the correct numeric type for the template
        _cast = int if template_cls is hou.IntParmTemplate else float

        # These require num_components; default to 1
        if default_value is not None:
            if not isinstance(default_value, (list, tuple)):
                default_value = [default_value]
            kwargs["num_components"] = len(default_value)
            kwargs["default_value"] = tuple(_cast(v) for v in default_value)
        else:
            kwargs["num_components"] = 1

        if min_val is not None:
            kwargs["min"] = _cast(min_val)
            kwargs["min_is_strict"] = False
        if max_val is not None:
            kwargs["max"] = _cast(max_val)
            kwargs["max_is_strict"] = False

        pt = template_cls(parm_name, label, **kwargs)

    elif template_cls is hou.StringParmTemplate:
        kwargs["num_components"] = 1
        if default_value is not None:
            if not isinstance(default_value, (list, tuple)):
                default_value = [default_value]
            kwargs["default_value"] = tuple(str(v) for v in default_value)
        pt = template_cls(parm_name, label, **kwargs)

    elif template_cls is hou.ToggleParmTemplate:
        dv = bool(default_value) if default_value is not None else False
        pt = template_cls(parm_name, label, default_value=dv)

    elif template_cls is hou.MenuParmTemplate:
        # For menu type, default_value should be a list of menu items
        items = (
            default_value if isinstance(default_value, (list, tuple)) else []
        )
        pt = template_cls(
            parm_name,
            label,
            menu_items=tuple(str(i) for i in items),
            menu_labels=tuple(str(i) for i in items),
        )
    else:
        pt = template_cls(parm_name, label, **kwargs)

    # Add to node
    ptg = node.parmTemplateGroup()
    ptg.addParmTemplate(pt)
    node.setParmTemplateGroup(ptg)

    return {
        "node_path": node_path,
        "parm_name": parm_name,
        "parm_type": parm_type,
        "label": label,
        "created": True,
    }


register_handler("parameters.create_spare_parameter", _create_spare_parameter)


###### Handler: parameters.create_spare_parameters


def _build_parm_template(spec: dict) -> hou.ParmTemplate:
    """Build a single ParmTemplate from a specification dict."""
    type_map: dict[str, type] = {
        "float": hou.FloatParmTemplate,
        "int": hou.IntParmTemplate,
        "string": hou.StringParmTemplate,
        "toggle": hou.ToggleParmTemplate,
        "menu": hou.MenuParmTemplate,
    }

    parm_name = spec["parm_name"]
    parm_type = spec["parm_type"].lower()
    label = spec["label"]
    default_value = spec.get("default_value")
    min_val = spec.get("min_val")
    max_val = spec.get("max_val")

    template_cls = type_map.get(parm_type)
    if template_cls is None:
        raise ValueError(
            f"Unsupported parm_type '{parm_type}' for parameter '{parm_name}'."
        )

    kwargs: dict[str, Any] = {}

    if template_cls in (hou.FloatParmTemplate, hou.IntParmTemplate):
        _cast = int if template_cls is hou.IntParmTemplate else float
        if default_value is not None:
            if not isinstance(default_value, (list, tuple)):
                default_value = [default_value]
            kwargs["num_components"] = len(default_value)
            kwargs["default_value"] = tuple(_cast(v) for v in default_value)
        else:
            kwargs["num_components"] = 1
        if min_val is not None:
            kwargs["min"] = _cast(min_val)
            kwargs["min_is_strict"] = False
        if max_val is not None:
            kwargs["max"] = _cast(max_val)
            kwargs["max_is_strict"] = False
        return template_cls(parm_name, label, **kwargs)

    if template_cls is hou.StringParmTemplate:
        kwargs["num_components"] = 1
        if default_value is not None:
            if not isinstance(default_value, (list, tuple)):
                default_value = [default_value]
            kwargs["default_value"] = tuple(str(v) for v in default_value)
        return template_cls(parm_name, label, **kwargs)

    if template_cls is hou.ToggleParmTemplate:
        dv = bool(default_value) if default_value is not None else False
        return template_cls(parm_name, label, default_value=dv)

    if template_cls is hou.MenuParmTemplate:
        items = (
            default_value if isinstance(default_value, (list, tuple)) else []
        )
        return template_cls(
            parm_name,
            label,
            menu_items=tuple(str(i) for i in items),
            menu_labels=tuple(str(i) for i in items),
        )

    return template_cls(parm_name, label, **kwargs)


_FOLDER_TYPE_MAP = {
    "Tabs": hou.folderType.Tabs,
    "tabs": hou.folderType.Tabs,
    "Collapsible": hou.folderType.Collapsible,
    "collapsible": hou.folderType.Collapsible,
    "Simple": hou.folderType.Simple,
    "simple": hou.folderType.Simple,
}


def _create_spare_parameters(
    node_path: str,
    parameters: list,
    folder_name: str | None = None,
    folder_type: str = "Tabs",
    **_: Any,
) -> dict[str, Any]:
    """Batch-create spare parameters, optionally inside a folder/tab."""
    node = _resolve_node(node_path)

    templates = []
    created = []
    for spec in parameters:
        pt = _build_parm_template(spec)
        templates.append(pt)
        created.append(spec["parm_name"])

    ptg = node.parmTemplateGroup()

    if folder_name is not None:
        ft = _FOLDER_TYPE_MAP.get(folder_type, hou.folderType.Tabs)
        folder = hou.FolderParmTemplate(
            folder_name.lower().replace(" ", "_"),
            folder_name,
            parm_templates=templates,
            folder_type=ft,
        )
        ptg.addParmTemplate(folder)
    else:
        for pt in templates:
            ptg.addParmTemplate(pt)

    node.setParmTemplateGroup(ptg)

    return {
        "node_path": node_path,
        "created": created,
        "count": len(created),
        "folder_name": folder_name,
    }


register_handler("parameters.create_spare_parameters", _create_spare_parameters)
