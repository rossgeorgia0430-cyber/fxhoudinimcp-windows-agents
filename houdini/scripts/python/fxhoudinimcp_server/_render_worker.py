"""Standalone hython worker that renders a ROP in a detached process.

This script is launched by the ``rendering.start_render_job`` handler via
``hython.exe`` so a long render runs OUTSIDE the interactive Houdini session
and never blocks its main thread or the MCP dispatcher's 120s budget.

Invocation::

    hython _render_worker.py <hip> <rop> <status_json> [a b c] [--button NAME]

Positional args:
    hip:          Path to the (sidecar) hip file to load and render from.
    rop:          Path to the ROP/Driver node to render.
    status_json:  Path the worker writes its JSON status to. The launching
                  handler polls this file via ``rendering.get_render_job``.

Optional args:
    a b c:        Frame range start / end / increment (floats). ``c`` is
                  optional and defaults to ``1.0``.
    --button NAME: Press this render button parm instead of calling
                  ``node.render()``. When ``--button`` is given with no NAME,
                  the worker auto-picks the first existing of the common
                  render-button names.

CAVEAT: hython runs with NO UI. HDA render buttons whose callback needs
``hou.ui`` (for example SideFX Labs "Vertex Animation Textures" "Render All")
may fail or no-op here. For those UI-dependent bakes use the synchronous
``rendering.render_rop`` handler with a generous ``timeout`` instead. This
worker is best for standard ROPs (Mantra/Karma/Alembic/geometry).

The worker is intentionally self-contained and defensive: it imports nothing
from the ``fxhoudinimcp_server`` package so it can run in a bare hython
environment, and every status transition is flushed to disk so a crashing
render still leaves an inspectable status file.
"""

from __future__ import annotations

# Built-in
import json
import os
import sys
import time
import traceback

# The list of button parm names tried, in order, when ``--button`` is passed
# without an explicit name. Mirrors the synchronous handler.
_BUTTON_CANDIDATES = ("renderall", "execute", "render", "rendersingle")

# Output-path parm names checked first, then any string parm whose name starts
# with one of ``_OUTPUT_PARM_PREFIXES``. Mirrors ``list_rop_outputs``.
_OUTPUT_PARM_NAMES = (
    "sopoutput",
    "copoutput",
    "lopoutput",
    "dopoutput",
    "picture",
    "vm_picture",
    "output",
    "filename",
    "file",
)
_OUTPUT_PARM_PREFIXES = ("path_", "output", "file")


def _write_status(status_path: str, payload: dict) -> None:
    """Write ``payload`` to ``status_path`` as JSON, flushing to disk.

    Best-effort: a failure to write status must never mask the render result,
    so write errors are swallowed after a diagnostic to stderr.
    """
    payload = dict(payload)
    payload.setdefault("updated_ts", time.time())
    try:
        out_dir = os.path.dirname(status_path)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        tmp_path = status_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, status_path)
    except OSError as exc:  # pragma: no cover - disk failure is non-fatal here
        sys.stderr.write(f"[_render_worker] could not write status: {exc}\n")
        sys.stderr.flush()


def _resolve_outputs(node) -> list:
    """Resolve every output-path parm on ``node`` to a stat-ed file entry.

    Defensive standalone copy of the ``list_rop_outputs`` logic so the worker
    has no dependency on the handler module. De-dupes by resolved path.
    """
    import hou

    outputs = []
    seen_paths = set()

    def _consider(parm) -> None:
        if parm is None:
            return
        try:
            raw = parm.eval()
        except Exception:  # noqa: BLE001 - a single bad parm must not abort
            return
        if not raw or not isinstance(raw, str):
            return
        try:
            resolved = hou.text.expandString(raw)
        except Exception:  # noqa: BLE001
            resolved = raw
        if not resolved or resolved in seen_paths:
            return
        seen_paths.add(resolved)

        exists = False
        size = None
        mtime = None
        try:
            if os.path.isfile(resolved):
                stat = os.stat(resolved)
                exists = True
                size = stat.st_size
                mtime = stat.st_mtime
        except OSError:
            pass

        outputs.append(
            {
                "parm": parm.name(),
                "path": resolved,
                "exists": exists,
                "size": size,
                "mtime": mtime,
            }
        )

    for parm_name in _OUTPUT_PARM_NAMES:
        _consider(node.parm(parm_name))

    try:
        all_parms = node.parms()
    except Exception:  # noqa: BLE001
        all_parms = ()
    for parm in all_parms:
        try:
            name = parm.name()
        except Exception:  # noqa: BLE001
            continue
        if name.startswith(_OUTPUT_PARM_PREFIXES):
            _consider(parm)

    return outputs


def _pick_button(node, requested: str | None):
    """Return the button parm to press, or raise ValueError if none exists."""
    if requested:
        parm = node.parm(requested)
        if parm is None:
            raise ValueError(
                f"Render button parm '{requested}' not found on {node.path()}."
            )
        return parm
    for candidate in _BUTTON_CANDIDATES:
        parm = node.parm(candidate)
        if parm is not None:
            return parm
    raise ValueError(
        f"No render button parm found on {node.path()}. Tried: "
        f"{list(_BUTTON_CANDIDATES)}."
    )


def _parse_args(argv: list) -> dict:
    """Parse the worker argv into a plain dict.

    Returns keys: ``hip``, ``rop``, ``status``, ``frame_range`` (list|None),
    ``use_button`` (bool), ``button`` (str|None).
    """
    if len(argv) < 3:
        raise SystemExit(
            "usage: _render_worker.py <hip> <rop> <status_json> "
            "[a b c] [--button NAME]"
        )

    hip = argv[0]
    rop = argv[1]
    status = argv[2]
    rest = list(argv[3:])

    use_button = False
    button = None
    if "--button" in rest:
        use_button = True
        idx = rest.index("--button")
        # A NAME follows --button only when it is not itself a frame number.
        if idx + 1 < len(rest):
            candidate = rest[idx + 1]
            is_number = False
            try:
                float(candidate)
                is_number = True
            except ValueError:
                is_number = False
            if not is_number:
                button = candidate
                del rest[idx : idx + 2]
            else:
                del rest[idx : idx + 1]
        else:
            del rest[idx : idx + 1]

    frame_range = None
    if rest:
        try:
            nums = [float(token) for token in rest]
        except ValueError as exc:
            raise SystemExit(f"Invalid frame range argument: {exc}") from exc
        if len(nums) >= 2:
            inc = nums[2] if len(nums) > 2 else 1.0
            frame_range = [nums[0], nums[1], inc]

    return {
        "hip": hip,
        "rop": rop,
        "status": status,
        "frame_range": frame_range,
        "use_button": use_button,
        "button": button,
    }


def main(argv: list) -> int:
    """Render the requested ROP, writing progress to the status file."""
    args = _parse_args(argv)
    status_path = args["status"]
    started_ts = time.time()

    base_status = {
        "hip": args["hip"],
        "rop": args["rop"],
        "frame_range": args["frame_range"],
        "use_button": args["use_button"],
        "button_parm": args["button"],
        "pid": os.getpid(),
        "started_ts": started_ts,
    }

    _write_status(status_path, {**base_status, "state": "running"})

    try:
        import hou
    except Exception as exc:  # noqa: BLE001
        _write_status(
            status_path,
            {
                **base_status,
                "state": "failed",
                "error": f"Could not import hou: {exc}",
                "traceback": traceback.format_exc(),
                "returncode": 1,
            },
        )
        return 1

    try:
        hou.hipFile.load(args["hip"], suppress_save_prompt=True)
    except Exception as exc:  # noqa: BLE001
        _write_status(
            status_path,
            {
                **base_status,
                "state": "failed",
                "error": f"Could not load hip '{args['hip']}': {exc}",
                "traceback": traceback.format_exc(),
                "returncode": 1,
            },
        )
        return 1

    node = hou.node(args["rop"])
    if node is None:
        _write_status(
            status_path,
            {
                **base_status,
                "state": "failed",
                "error": f"ROP node not found: {args['rop']}",
                "returncode": 1,
            },
        )
        return 1

    used_button = False
    button_name = None
    try:
        if args["use_button"]:
            parm = _pick_button(node, args["button"])
            used_button = True
            button_name = parm.name()
            parm.pressButton()
        elif args["frame_range"] is not None:
            fr = args["frame_range"]
            node.render(
                frame_range=(float(fr[0]), float(fr[1]), float(fr[2])),
                verbose=False,
            )
        else:
            node.render(verbose=False)
    except Exception as exc:  # noqa: BLE001
        try:
            errors = list(node.errors())
        except Exception:  # noqa: BLE001
            errors = []
        _write_status(
            status_path,
            {
                **base_status,
                "state": "failed",
                "used_button": used_button,
                "button_parm": button_name,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "errors": errors,
                "returncode": 1,
            },
        )
        return 1

    try:
        errors = list(node.errors())
    except Exception:  # noqa: BLE001
        errors = []
    try:
        warnings = list(node.warnings())
    except Exception:  # noqa: BLE001
        warnings = []

    outputs = _resolve_outputs(node)

    _write_status(
        status_path,
        {
            **base_status,
            "state": "done",
            "used_button": used_button,
            "button_parm": button_name,
            "returncode": 0,
            "elapsed": time.time() - started_ts,
            "errors": errors,
            "warnings": warnings,
            "outputs": outputs,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
