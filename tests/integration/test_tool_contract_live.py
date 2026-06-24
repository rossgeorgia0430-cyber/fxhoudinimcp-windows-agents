"""Live contract: the complete MCP tool surface equals registered handlers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import fxhoudinimcp_server.dispatcher as dispatcher


pytestmark = pytest.mark.integration

TOOLS_DIR = Path(__file__).resolve().parents[2] / "python" / "fxhoudinimcp" / "tools"
SPECIAL_MCP_TOOLS = {"get_houdini_connection_status"}


def _wrapper_commands() -> set[str]:
    commands: set[str] = set()
    for path in TOOLS_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in tree.body:
            if not isinstance(function, ast.AsyncFunctionDef):
                continue
            is_tool = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                for decorator in function.decorator_list
            )
            if not is_tool:
                continue
            if function.name in SPECIAL_MCP_TOOLS:
                continue
            command_literals = {
                call.args[0].value
                for call in ast.walk(function)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "execute"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            }
            assert len(command_literals) == 1, (function.name, command_literals)
            commands.update(command_literals)
    return commands


def test_all_mcp_wrapper_commands_are_live_registered_handlers():
    wrapped = _wrapper_commands()
    registered = set(dispatcher.list_commands())
    assert len(wrapped) == 200
    assert wrapped == registered
