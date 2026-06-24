"""Real FastMCP tool-surface profiles.

The Houdini plugin always loads every dispatcher handler.  The external MCP
process can expose a smaller, task-oriented subset so clients do not have to
rank 201 schemas on every turn.  Filtering happens against FastMCP's actual
``ToolManager`` after decorators have registered the complete wrapper catalog.
"""

from __future__ import annotations

# Built-in
import logging
import os
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.tools.base import Tool

logger = logging.getLogger(__name__)

PROFILE_ENV_VAR = "FXHOUDINIMCP_TOOL_PROFILE"
PROFILE_ENV_ALIASES = ("HOUDINI_MCP_TOOL_PROFILE",)
DEFAULT_PROFILE = "core"

_CORE_MODULES = frozenset(
    {
        "scene",
        "nodes",
        "parameters",
        "graph",
        "help",
        "context",
        "viewport",
        "code",
        "geometry",
        "image",
    }
)

_PROFILE_MODULES: dict[str, frozenset[str] | None] = {
    "core": _CORE_MODULES,
    "modeling": _CORE_MODULES
    | {
        "geometry",
        "vex",
        "materials",
        "animation",
        "hda",
    },
    "simulation": _CORE_MODULES
    | {
        "geometry",
        "dops",
        "cache",
        "chops",
        "tops",
        "animation",
    },
    "usd-render": _CORE_MODULES
    | {
        "lops",
        "materials",
        "rendering",
        "takes",
        "animation",
        "cops",
    },
    "full": None,
}

# Composite workflows span several domains, so classify them by operation
# instead of exposing the whole workflows module in every specialized profile.
_PROFILE_WORKFLOW_TOOLS: dict[str, frozenset[str]] = {
    "core": frozenset(),
    "modeling": frozenset(
        {
            "create_material",
            "assign_material",
            "build_sop_chain",
        }
    ),
    "simulation": frozenset(
        {
            "setup_pyro_sim",
            "setup_rbd_sim",
            "setup_flip_sim",
            "setup_vellum_sim",
        }
    ),
    "usd-render": frozenset(
        {
            "create_material",
            "assign_material",
            "setup_render",
        }
    ),
    "full": frozenset(),
}

# High-value tools defined in a non-core module that should still surface in
# focused profiles. Unlike _PROFILE_WORKFLOW_TOOLS, these match by tool name
# regardless of the defining module, so the render/bake helpers in the
# ``rendering`` module appear in ``core`` without pulling in the whole
# rendering surface.
_RENDER_HELPER_TOOLS = frozenset(
    {
        "render_rop",
        "list_rop_outputs",
        "start_render_job",
        "get_render_job",
        "list_render_jobs",
        "cancel_render_job",
    }
)

_PROFILE_EXTRA_TOOLS: dict[str, frozenset[str]] = {
    "core": _RENDER_HELPER_TOOLS,
    "modeling": _RENDER_HELPER_TOOLS,
    "simulation": _RENDER_HELPER_TOOLS,
    "usd-render": frozenset(),  # rendering module already included
    "full": frozenset(),
}

PROFILE_DESCRIPTIONS = {
    "core": "Scene, node, parameter, graph, geometry inspection, context, viewport, help, and fallback code tools.",
    "modeling": "Core plus SOP geometry, VEX, materials, animation, HDAs, and modeling workflows.",
    "simulation": "Core plus geometry, DOPs, caches, CHOPs, TOPs, animation, and simulation workflows.",
    "usd-render": "Core plus Solaris/USD, materials, rendering, takes, COPs, animation, and render workflows.",
    "full": "The complete MCP wrapper catalog.",
}

_active_profile_status: dict[str, Any] = {
    "name": DEFAULT_PROFILE,
    "requested": DEFAULT_PROFILE,
    "env_var": PROFILE_ENV_VAR,
    "applied": False,
}


def resolve_tool_profile(environ: dict[str, str] | None = None) -> tuple[str, str, str | None]:
    """Resolve the requested profile with deterministic fallback.

    Returns ``(active, requested, fallback_reason)``.  The primary environment
    variable wins; the older generic alias is accepted to ease migration.
    """
    env = os.environ if environ is None else environ
    requested = env.get(PROFILE_ENV_VAR)
    if requested is None:
        requested = next(
            (env[name] for name in PROFILE_ENV_ALIASES if env.get(name)),
            DEFAULT_PROFILE,
        )
    requested = requested.strip().lower()
    if requested in _PROFILE_MODULES:
        return requested, requested, None
    reason = (
        f"Unknown tool profile {requested!r}; falling back to "
        f"{DEFAULT_PROFILE!r}. Valid profiles: {', '.join(_PROFILE_MODULES)}."
    )
    return DEFAULT_PROFILE, requested, reason


def _tool_module(tool: Tool) -> str:
    return tool.fn.__module__.rsplit(".", 1)[-1]


def select_profile_tools(profile: str, tools: Iterable[Tool]) -> set[str]:
    """Return exact FastMCP tool names selected by ``profile``."""
    if profile not in _PROFILE_MODULES:
        raise ValueError(f"Unknown tool profile: {profile}")
    tool_list = list(tools)
    modules = _PROFILE_MODULES[profile]
    if modules is None:
        return {tool.name for tool in tool_list}

    workflow_tools = _PROFILE_WORKFLOW_TOOLS[profile]
    extra_tools = _PROFILE_EXTRA_TOOLS.get(profile, frozenset())
    return {
        tool.name
        for tool in tool_list
        if _tool_module(tool) in modules
        or tool.name in extra_tools
        or (_tool_module(tool) == "workflows" and tool.name in workflow_tools)
    }


def apply_tool_profile(mcp: FastMCP) -> dict[str, Any]:
    """Filter FastMCP's registered tools and return the applied profile status."""
    # FastMCP has no synchronous public list API.  Using its ToolManager for the
    # snapshot, then the public remove_tool API for mutation, makes filtering
    # affect both tools/list and tools/call rather than merely documentation.
    registered_tools = list(mcp._tool_manager.list_tools())
    profile, requested, fallback_reason = resolve_tool_profile()
    selected_names = select_profile_tools(profile, registered_tools)

    for tool in registered_tools:
        if tool.name not in selected_names:
            mcp.remove_tool(tool.name)

    profile_counts = {
        name: len(select_profile_tools(name, registered_tools))
        for name in _PROFILE_MODULES
    }
    modules = _PROFILE_MODULES[profile]
    status: dict[str, Any] = {
        "name": profile,
        "requested": requested,
        "default": DEFAULT_PROFILE,
        "env_var": PROFILE_ENV_VAR,
        "env_aliases": list(PROFILE_ENV_ALIASES),
        "applied": True,
        "tool_count": len(selected_names),
        "full_tool_count": len(registered_tools),
        "hidden_tool_count": len(registered_tools) - len(selected_names),
        "included_families": (
            ["all"] if modules is None else sorted(modules)
        ),
        "profile_counts": profile_counts,
        "available_profiles": {
            name: PROFILE_DESCRIPTIONS[name] for name in _PROFILE_MODULES
        },
    }
    if fallback_reason:
        status["fallback_reason"] = fallback_reason
        logger.warning(fallback_reason)

    _active_profile_status.clear()
    _active_profile_status.update(status)
    logger.info(
        "Applied MCP tool profile '%s': %d/%d tools",
        profile,
        len(selected_names),
        len(registered_tools),
    )
    return dict(status)


def get_active_tool_profile() -> dict[str, Any]:
    """Return a copy of the current MCP process profile metadata."""
    return dict(_active_profile_status)


def profile_names() -> tuple[str, ...]:
    """Return supported profile names in stable display order."""
    return tuple(_PROFILE_MODULES)
