"""Standalone hython worker that dumps part of a HIP file's node network as JSON.

Launched by the ``diagnostics.inspect_hip_file`` handler so another scene can
be read without loading it into the interactive session.

Invocation::

    hython _hip_inspect_worker.py <hip> <root_path> <max_depth> <max_nodes> <out_json>

Each node reports its type, inputs, flags, and only the parameters that differ
from their defaults (values, expressions, key counts). Nodes inside a locked
asset are skipped unless they sit in an editable section of it. The worker
imports nothing from the ``fxhoudinimcp_server`` package so it runs in a bare
hython environment.
"""

from __future__ import annotations

# Built-in
import json
import sys

# Third-party
import hou

# Long VEX snippets and Python callbacks are the usual reason to inspect a
# reference scene, so keep a generous prefix of each string value.
_MAX_STRING_LENGTH = 4000
_VALUELESS_TEMPLATES = (
    hou.parmTemplateType.FolderSet,
    hou.parmTemplateType.Folder,
    hou.parmTemplateType.Button,
    hou.parmTemplateType.Separator,
    hou.parmTemplateType.Label,
)


def _parm_entry(parm: hou.Parm) -> dict:
    if parm.parmTemplate().type() == hou.parmTemplateType.String:
        try:
            value = parm.unexpandedString()
        except hou.OperationFailed:
            # Keyframed string parms have no single unexpanded value.
            value = parm.evalAsString()
    else:
        value = parm.eval()
    if not isinstance(value, (int, float, str)):
        value = str(value)
    if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
        value = value[:_MAX_STRING_LENGTH] + "..."
    entry = {"value": value}
    try:
        entry["expression"] = parm.expression()
    except hou.OperationFailed:
        pass  # No expression on this parm.
    key_count = len(parm.keyframes())
    if key_count > 1:
        entry["keyframes"] = key_count
    return entry


def _node_entry(node: hou.Node) -> dict:
    entry = {
        "path": node.path(),
        "type": node.type().nameWithCategory(),
        "inputs": [source.path() if source else None for source in node.inputs()],
    }
    if hasattr(node, "isBypassed") and node.isBypassed():
        entry["bypassed"] = True
    if hasattr(node, "isDisplayFlagSet"):
        entry["display"] = node.isDisplayFlagSet()
    entry["parms"] = {
        parm.name(): _parm_entry(parm)
        for parm in node.parms()
        if parm.parmTemplate().type() not in _VALUELESS_TEMPLATES and not parm.isAtDefault()
    }
    return entry


def _collect(node: hou.Node, depth: int, max_depth: int, max_nodes: int, entries: list) -> bool:
    """Append ``node`` and its descendants authored in this scene; True when truncated.

    Locked asset internals are walked but not reported, because an editable
    section (such as a solver's forces subnet) can sit below them.
    """
    is_authored = depth == 0 or not node.isInsideLockedHDA() or node.isEditableInsideLockedHDA()
    if is_authored:
        if len(entries) >= max_nodes:
            return True
        entries.append(_node_entry(node))
    if depth >= max_depth:
        return False
    for child in node.children():
        if _collect(child, depth + 1, max_depth, max_nodes, entries):
            return True
    return False


def main(hip_path: str, root_path: str, max_depth: int, max_nodes: int, out_path: str) -> None:
    try:
        hou.hipFile.load(hip_path, suppress_save_prompt=True)
        load_warnings = None
    except hou.LoadWarning as exc:
        # The scene is still loaded; the warnings are part of the report.
        load_warnings = str(exc)
    root = hou.node(root_path)
    if root is None:
        raise SystemExit(f"Node not found in {hip_path}: {root_path}")
    entries: list = []
    truncated = _collect(root, 0, max_depth, max_nodes, entries)
    report = {
        "hip_path": hip_path,
        "houdini_version": hou.applicationVersionString(),
        "fps": hou.fps(),
        "frame_range": list(hou.playbar.frameRange()),
        "root_path": root_path,
        "load_warnings": load_warnings,
        "node_count": len(entries),
        "truncated": truncated,
        "nodes": entries,
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
