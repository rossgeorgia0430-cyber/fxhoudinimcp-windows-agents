"""hwebserver endpoint registration for the FXHoudini-MCP plugin.

Registers API functions on Houdini's built-in HTTP server that the
external MCP server communicates with over HTTP.

Calling convention (JSON-encoded RPC):
    POST /api
    Body: json=["mcp.execute", [], {"command": "...", "params": {...}, "request_id": "..."}]

hwebserver auto-serialises the returned dict/list to JSON.
"""

from __future__ import annotations

# Built-in
import os

# Third-party
import hwebserver

# Internal
from fxhoudinimcp_server import dispatcher


@hwebserver.apiFunction(namespace="mcp")
def execute(request, command="", params=None, request_id="", command_timeout=None):
    """Single entry point for all MCP tool calls.

    Args:
        request: hwebserver.Request (always first arg).
        command: Dotted command name (e.g. "scene.get_scene_info").
        params: Tool-specific parameters dict.
        request_id: Correlation ID echoed back in the response.
        command_timeout: Optional per-call main-thread budget in seconds for
            long operations (render, cache, sim). Falls back to the dispatcher
            default when omitted.
    """
    if params is None:
        params = {}

    result = dispatcher.dispatch(command, params, timeout=command_timeout)
    result["request_id"] = request_id
    return result


@hwebserver.apiFunction(namespace="mcp")
def health(request):
    """Health check endpoint with non-mutating runtime capability discovery."""
    import hou

    from fxhoudinimcp_server import handlers
    from fxhoudinimcp_server.runtime_capabilities import collect_runtime_capabilities

    handler_status = handlers.capability_status()

    def _collect():
        runtime = collect_runtime_capabilities(
            hou,
            commands=dispatcher.list_commands(),
            handler_status=handler_status,
        )
        return {
            "houdini_version": runtime["houdini"]["version"],
            "hip_file": hou.hipFile.name(),
            "ui_available": runtime["houdini"]["ui_available"],
            "runtime_capabilities": runtime,
        }

    # hwebserver runs this endpoint on a worker thread.  Node type/category
    # introspection is read-only but still belongs on Houdini's main thread.
    try:
        ui_available = bool(hou.isUIAvailable())
        if ui_available:
            import hdefereval

            probed = hdefereval.executeInMainThreadWithResult(_collect)
        else:
            probed = _collect()
    except Exception as exc:
        # Connection health must remain available even when an optional probe
        # or main-thread handoff fails.  Return a small schema-compatible
        # degraded result so the client can explain what could not be trusted.
        probed = {
            "houdini_version": "unknown",
            "hip_file": "unknown",
            "ui_available": False,
            "runtime_capabilities": {
                "schema_version": 1,
                "runtime_status": "degraded",
                "houdini": {
                    "version": "unknown",
                    "version_tuple": None,
                    "ui_available": False,
                },
                "handlers": {
                    "ready": not handler_status.get("handler_modules_failed"),
                    "modules_failed": list(
                        handler_status.get("handler_modules_failed", [])
                    ),
                    "failures": list(
                        handler_status.get("handler_module_failures", [])
                    ),
                    "command_count": handler_status.get("command_count", 0),
                },
                "probe_errors": [
                    {
                        "probe": "health_main_thread",
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                ],
            },
        }

    result = {"status": "ok", "pid": os.getpid(), **probed}
    result.update(handler_status)
    return result


@hwebserver.apiFunction(namespace="mcp")
def list_commands(request):
    """List all registered command names for introspection."""
    return {"commands": dispatcher.list_commands()}
