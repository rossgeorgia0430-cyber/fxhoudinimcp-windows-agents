"""Non-mutating Houdini runtime capability probes for MCP health responses."""

from __future__ import annotations

# Built-in
import importlib.util
import os
import platform
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_CATEGORY_KEYS = {
    "objects": ("Object",),
    "sop": ("Sop",),
    "dop": ("Dop",),
    "lop": ("Lop",),
    "top": ("Top",),
    "cop": ("Cop", "Cop2"),
    "chop": ("Chop",),
    "rop": ("Driver",),
    "vop": ("Vop",),
}

_HELP_ARCHIVES = (
    "nodes.zip",
    "hom.zip",
    "vex.zip",
    "expressions.zip",
)


def _safe_error(probe: str, exc: Exception) -> dict[str, str]:
    return {
        "probe": probe,
        "type": type(exc).__name__,
        "message": str(exc),
    }


def _enum_name(value: object) -> str:
    name = getattr(value, "name", None)
    if callable(name):
        try:
            return str(name())
        except Exception:
            pass
    if name is not None:
        return str(name)
    return str(value)


def _find_category(
    categories: dict[str, object],
    aliases: tuple[str, ...],
) -> tuple[str | None, object | None]:
    for alias in aliases:
        if alias in categories:
            return alias, categories[alias]
    lowered = {str(name).lower(): (str(name), category) for name, category in categories.items()}
    for alias in aliases:
        match = lowered.get(alias.lower())
        if match:
            return match
    return None, None


def _node_type_names(category: object) -> set[str]:
    node_types = category.nodeTypes()
    return {str(name) for name in node_types}


def _renderer_status(category_types: dict[str, set[str]]) -> dict[str, Any]:
    rop = {name.lower() for name in category_types.get("rop", set())}
    lop = {name.lower() for name in category_types.get("lop", set())}
    combined = rop | lop

    def has_exact(*names: str) -> bool:
        return any(name in combined for name in names)

    renderers = {
        "karma": has_exact(
            "karma",
            "karmarendersettings",
            "usdrender_rop",
            "usd_render",
        ),
        "mantra": has_exact("ifd"),
        "redshift": any(name.startswith("redshift") for name in combined),
        "arnold": any(name == "arnold" or name.startswith("arnold_") for name in combined),
        "renderman": has_exact("ris", "renderman", "prman"),
    }
    installed = sorted(name for name, available in renderers.items() if available)
    return {
        "available": bool(installed),
        "installed": installed,
        "known_renderer_types": renderers,
        "license_verified": False,
        "license_note": (
            "Node-type availability is non-mutating; renderer license checkout "
            "is intentionally not attempted by health probes."
        ),
    }


def _handler_summary(
    commands: Iterable[str],
    handler_status: dict[str, Any],
) -> dict[str, Any]:
    command_list = sorted(set(commands))
    prefix_counts = Counter(command.split(".", 1)[0] for command in command_list)
    failures = handler_status.get("handler_module_failures", [])
    if not failures:
        failures = [
            {"module": name, "type": "ImportError", "message": "Module failed to load."}
            for name in handler_status.get("handler_modules_failed", [])
        ]
    return {
        "ready": not failures,
        "module_total": handler_status.get("handler_modules_total"),
        "modules_loaded": list(handler_status.get("handler_modules_loaded", [])),
        "modules_failed": list(handler_status.get("handler_modules_failed", [])),
        "failures": list(failures),
        "command_count": len(command_list),
        "command_categories": dict(sorted(prefix_counts.items())),
    }


def collect_runtime_capabilities(
    hou_module: object,
    *,
    commands: Iterable[str] = (),
    handler_status: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Collect safe, read-only capability signals from a running Houdini.

    No nodes are created, no renderer is started, and no license checkout is
    requested.  Individual probe failures are returned as structured data so
    one optional subsystem cannot make the health endpoint unusable.
    """
    env = os.environ if environ is None else environ
    handlers = _handler_summary(commands, handler_status or {})
    errors: list[dict[str, str]] = []

    version = "unknown"
    version_tuple: list[int] | None = None
    ui_available = False
    try:
        version = str(hou_module.applicationVersionString())
        application_version = getattr(hou_module, "applicationVersion", None)
        if callable(application_version):
            version_tuple = [int(item) for item in application_version()]
    except Exception as exc:
        errors.append(_safe_error("houdini_version", exc))
    try:
        ui_available = bool(hou_module.isUIAvailable())
    except Exception as exc:
        errors.append(_safe_error("ui_available", exc))

    license_info: dict[str, Any] = {
        "available": False,
        "category": "unknown",
        "renderer_license_verified": False,
    }
    try:
        category = hou_module.licenseCategory()
        category_name = _enum_name(category)
        license_info.update(
            {
                "available": True,
                "category": category_name,
                "is_noncommercial": any(
                    marker in category_name.lower()
                    for marker in ("apprentice", "noncommercial", "education")
                ),
                "note": (
                    "Reports the running Houdini application license category; "
                    "optional renderer entitlements are not checked out."
                ),
            }
        )
    except Exception as exc:
        errors.append(_safe_error("license_category", exc))
        license_info["error"] = _safe_error("license_category", exc)

    category_status: dict[str, dict[str, Any]] = {}
    category_types: dict[str, set[str]] = {}
    try:
        categories = dict(hou_module.nodeTypeCategories())
    except Exception as exc:
        categories = {}
        errors.append(_safe_error("node_type_categories", exc))

    for capability_name, aliases in _CATEGORY_KEYS.items():
        category_name, category = _find_category(categories, aliases)
        if category is None:
            category_status[capability_name] = {
                "available": False,
                "houdini_category": None,
                "node_type_count": 0,
            }
            category_types[capability_name] = set()
            continue
        try:
            names = _node_type_names(category)
            category_types[capability_name] = names
            category_status[capability_name] = {
                "available": bool(names),
                "houdini_category": category_name,
                "node_type_count": len(names),
            }
        except Exception as exc:
            category_types[capability_name] = set()
            category_status[capability_name] = {
                "available": False,
                "houdini_category": category_name,
                "node_type_count": 0,
                "error": _safe_error(f"node_types.{capability_name}", exc),
            }
            errors.append(_safe_error(f"node_types.{capability_name}", exc))

    hfs = env.get("HFS", "")
    if not hfs:
        try:
            hfs = str(hou_module.expandString("$HFS"))
        except Exception as exc:
            errors.append(_safe_error("hfs_path", exc))
    help_root = Path(hfs) / "houdini" / "help" if hfs else None
    available_archives = (
        sorted(name for name in _HELP_ARCHIVES if (help_root / name).is_file())
        if help_root
        else []
    )
    help_info = {
        "available": bool(available_archives),
        "root_exists": bool(help_root and help_root.is_dir()),
        "archive_count": len(available_archives),
        "archives": available_archives,
        "handler_available": handlers["command_categories"].get("help", 0) > 0,
    }

    try:
        usd_python_available = importlib.util.find_spec("pxr") is not None
    except (ImportError, ValueError) as exc:
        usd_python_available = False
        errors.append(_safe_error("usd_python_module", exc))

    renderers = _renderer_status(category_types)
    command_categories = handlers["command_categories"]

    def subsystem(
        category: str | None,
        command_prefix: str,
        *,
        extra: bool = True,
        requires_ui: bool = False,
    ) -> dict[str, Any]:
        category_ready = True if category is None else category_status[category]["available"]
        handler_ready = command_categories.get(command_prefix, 0) > 0
        available = category_ready and handler_ready and extra and (
            ui_available if requires_ui else True
        )
        return {
            "available": available,
            "category_available": category_ready,
            "handler_available": handler_ready,
            "requires_ui": requires_ui,
        }

    subsystems = {
        "core_scene": subsystem(None, "scene"),
        "modeling": subsystem("sop", "geometry"),
        "simulation": subsystem("dop", "dops"),
        "usd": {
            **subsystem("lop", "lops", extra=usd_python_available),
            "pxr_available": usd_python_available,
        },
        "rendering": {
            **subsystem("rop", "rendering", extra=renderers["available"]),
            "renderer_available": renderers["available"],
        },
        "pdg": subsystem("top", "tops"),
        "compositing": subsystem("cop", "cops"),
        "animation": subsystem(None, "animation"),
        "viewport": subsystem(None, "viewport", requires_ui=True),
        "help": {
            "available": help_info["available"] and help_info["handler_available"],
            "handler_available": help_info["handler_available"],
            "content_available": help_info["available"],
            "requires_ui": False,
        },
    }

    core_ready = (
        handlers["ready"]
        and command_categories.get("scene", 0) > 0
        and command_categories.get("nodes", 0) > 0
        and command_categories.get("parameters", 0) > 0
    )
    return {
        "schema_version": 1,
        "runtime_status": "ready" if core_ready else "degraded",
        "houdini": {
            "version": version,
            "version_tuple": version_tuple,
            "ui_available": ui_available,
            "python_version": platform.python_version(),
            "platform": sys.platform,
        },
        "handlers": handlers,
        "license": license_info,
        "node_categories": category_status,
        "subsystems": subsystems,
        "usd": {
            "available": subsystems["usd"]["available"],
            "pxr_available": usd_python_available,
            "lop_category_available": category_status["lop"]["available"],
        },
        "help": help_info,
        "renderers": renderers,
        "probe_errors": errors,
    }
