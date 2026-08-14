"""Auto-start FXHoudini-MCP server when Houdini's UI is ready.

This script is sourced by Houdini once at startup, after the UI is
initialised.  Unlike scripts/456.py it stacks correctly with other
packages that also define a uiready.py.

Houdini 22 ships Python 3.13; without this python3.13libs copy the
plugin would not auto-start on 22.x.

Set FXHOUDINIMCP_AUTOSTART=0 to disable auto-start.
"""

import os


def _start_fxhoudinimcp() -> None:
    try:
        import fxhoudinimcp_server.startup

        fxhoudinimcp_server.startup.ensure_running()
    except Exception as e:
        print(f"[fxhoudinimcp] Auto-start failed: {e}")


if os.environ.get("FXHOUDINIMCP_AUTOSTART", "1") == "1":
    # Houdini 22 segfaults if hwebserver.run() is called synchronously from
    # uiready (UI is "ready" but the event loop is not). Defer one tick.
    try:
        import hdefereval

        hdefereval.executeDeferred(_start_fxhoudinimcp)
    except Exception:
        _start_fxhoudinimcp()
