"""Main-thread dispatch mechanism for executing hou.* calls safely.

Houdini requires all hou.* API calls to run on the main thread.
hwebserver handlers run on worker threads, so we use
hdefereval.executeInMainThreadWithResult() to marshal calls
to the main thread and block until they complete.
"""

from __future__ import annotations

# Built-in
import logging
import threading
import time
import traceback
from difflib import get_close_matches
from typing import Any, Callable

# Third-party. Hython can expose hdefereval too, but without a UI event loop
# it cannot service executeInMainThreadWithResult; use the direct fallback
# there so HTTP bridge tests and headless deployments do not 502.
try:
    import hdefereval

    try:
        import hou

        HAS_HDEFEREVAL = bool(hou.isUIAvailable())
    except ImportError:
        # Unit tests provide an hdefereval double without a Houdini module.
        HAS_HDEFEREVAL = True
except ImportError:
    HAS_HDEFEREVAL = False

logger = logging.getLogger(__name__)

###### Constants

_COMMAND_TIMEOUT = 120  # seconds

# Registry of command name -> handler function
_handler_registry: dict[str, Callable] = {}


def register_handler(command: str, handler: Callable) -> None:
    """Register a handler function for a command name.

    Args:
        command: Dotted command name (e.g. "scene.get_scene_info")
        handler: Function to call with **params
    """
    _handler_registry[command] = handler


def list_commands() -> list[str]:
    """Return all registered command names."""
    return sorted(_handler_registry.keys())


def dispatch(
    command: str, params: dict[str, Any], timeout: float | None = None
) -> dict[str, Any]:
    """Execute a command on the main thread and return the result.

    This is called from hwebserver worker threads. It uses
    hdefereval.executeInMainThreadWithResult() to safely execute
    hou.* calls on the main thread.

    Args:
        command: The command name to execute
        params: Parameters to pass to the handler
        timeout: Optional main-thread budget in seconds (for long ops like
            render/cache/sim). Defaults to ``_COMMAND_TIMEOUT``.

    Returns:
        A response dict with "status", "data"/"error", and "timing_ms" keys.
    """
    handler = _handler_registry.get(command)
    if handler is None:
        close = get_close_matches(command, list(_handler_registry), n=5, cutoff=0.4)
        return {
            "status": "error",
            "error": {
                "code": "UNKNOWN_COMMAND",
                "message": (
                    f"No handler registered for command: {command}."
                    + (f" Did you mean: {close}?" if close else "")
                ),
                "command_count": len(_handler_registry),
            },
        }

    try:
        command_timeout = float(timeout) if timeout else _COMMAND_TIMEOUT
    except (TypeError, ValueError):
        command_timeout = _COMMAND_TIMEOUT

    start_time = time.time()

    def _execute():
        try:
            result = handler(**params)
            return {"status": "success", "data": result}
        except Exception as e:
            logger.exception("Command '%s' failed", command)
            return {
                "status": "error",
                "error": {
                    "code": type(e).__name__,
                    "message": str(e),
                    "retryable": False,
                },
            }

    try:
        if HAS_HDEFEREVAL:
            # Run hdefereval call in a worker thread so we can enforce a timeout
            container: dict[str, Any] = {}

            def _run():
                try:
                    container["result"] = hdefereval.executeInMainThreadWithResult(_execute)
                except Exception as exc:
                    container["error"] = exc
                    container["tb"] = traceback.format_exc()

            worker = threading.Thread(target=_run, daemon=True)
            worker.start()
            worker.join(timeout=command_timeout)

            if worker.is_alive():
                logger.error(
                    "Command '%s' timed out after %s seconds", command, command_timeout
                )
                result = {
                    "status": "error",
                    "error": {
                        "code": "TIMEOUT",
                        "message": (
                            f"Command '{command}' did not complete within "
                            f"{command_timeout:.0f}s and is still running on the "
                            "main thread. Its completion is UNKNOWN — do not "
                            "blindly retry a write (it may double-apply); inspect "
                            "the scene, or re-issue with a larger `timeout`."
                        ),
                        "completion": "unknown",
                        "retryable": False,
                        "timeout": command_timeout,
                    },
                }
            elif "error" in container:
                logger.error(
                    "Main-thread dispatch failed for '%s': %s\n%s",
                    command,
                    container["error"],
                    container.get("tb", ""),
                )
                result = {
                    "status": "error",
                    "error": {
                        "code": "DISPATCH_ERROR",
                        "message": f"Failed to dispatch to main thread: {container['error']}",
                        "retryable": False,
                    },
                }
            else:
                result = container["result"]
        else:
            # Fallback for hython (single-threaded, no hdefereval needed)
            result = _execute()
    except Exception as e:
        logger.exception("Dispatcher failed for '%s'", command)
        result = {
            "status": "error",
            "error": {
                "code": "DISPATCH_ERROR",
                "message": f"Failed to dispatch to main thread: {e}",
                "retryable": False,
            },
        }

    result["timing_ms"] = round((time.time() - start_time) * 1000, 2)
    return result
