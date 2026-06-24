"""MCP tools for Houdini's shipped documentation.

Search and retrieval over the manual that ships inside the RUNNING
Houdini install — always version-exact, available offline, and served
on demand so it costs no context until a question actually comes up.
"""

from __future__ import annotations

# Built-in
from typing import Any

# Third-party
from mcp.server.fastmcp import Context

# Internal
from fxhoudinimcp.server import _get_bridge, mcp


@mcp.tool()
async def search_help(
    ctx: Context,
    query: str,
    scope: str | None = None,
    limit: int = 10,
) -> dict:
    """Search the running Houdini's own documentation — concepts,
    workflows, VEX functions, expression functions, HOM API, Solaris,
    TOPs. Version-exact, straight from the install.

    Use this BEFORE improvising: when unsure how a workflow is meant to
    be done ("pyro shaping", "vellum constraints"), what a VEX or
    expression function does, or what a Solaris/TOPs concept means.
    Follow up with get_help_page on a result path.

    Args:
        query: Search words (all must match a page).
        scope: Optional corpus — "nodes", "vex", "expressions", "hom",
            "solaris", "tops", "character", "ref", "shelf".
        limit: Max results.
    """
    bridge = _get_bridge(ctx)
    params: dict[str, Any] = {"query": query, "limit": limit}
    if scope is not None:
        params["scope"] = scope
    return await bridge.execute("help.search_help", params)


@mcp.tool()
async def get_help_page(ctx: Context, path: str) -> dict:
    """Fetch one page of Houdini's shipped documentation by path.

    Read the real reference instead of writing from memory — especially
    the VEX function pages (vex/functions/...) before any justified
    wrangle, and expression pages (expressions/...) before channel
    expressions.

    Args:
        path: As returned by search_help — e.g. "nodes/sop/scatter",
            "vex/functions/noise", "expressions/ch".
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute("help.get_help_page", {"path": path})


@mcp.tool()
async def get_hda_help(
    ctx: Context,
    node_type: str,
    context: str = "Sop",
) -> dict:
    """Read help directly off an installed node/HDA definition — including
    third-party HDAs (SideFX Labs, studio tools) the shipped docs never
    cover.

    Returns the type description, its embedded help page, and per-parameter
    help strings. Note: many third-party HDAs ship an empty embedded_help;
    in that case the description and parm_help are still useful and are
    returned anyway. For native nodes documented in the manual, search_help
    / get_help_page give richer prose.

    Args:
        node_type: Type name, e.g. "labs::mountain" or "scatter".
        context: Type category — "Sop", "Object", "Driver", "Lop", "Dop",
            "Cop2", "Vop", "Chop", or "Top" (default "Sop").
    """
    bridge = _get_bridge(ctx)
    return await bridge.execute(
        "help.get_hda_help",
        {
            "node_type": node_type,
            "context": context,
        },
    )
