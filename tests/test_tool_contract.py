"""Ensure every registered MCP tool maps to one Houdini dispatcher command."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import fxhoudinimcp.tools  # noqa: F401  (registers all tools)
from fxhoudinimcp.server import mcp


TOOLS_DIR = Path(__file__).resolve().parents[1] / "python" / "fxhoudinimcp" / "tools"
SPECIAL_MCP_TOOLS = {"get_houdini_connection_status"}


def _is_mcp_tool(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "tool"
    )


def _command_literal(function: ast.AsyncFunctionDef) -> str:
    commands: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "execute":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            commands.add(node.args[0].value)
    assert len(commands) == 1, f"{function.name} must delegate to exactly one command, got {commands}"
    return commands.pop()


def wrapper_commands() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in TOOLS_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and any(
                _is_mcp_tool(decorator) for decorator in node.decorator_list
            ):
                assert node.name not in mapping, f"duplicate tool name {node.name}"
                if node.name not in SPECIAL_MCP_TOOLS:
                    mapping[node.name] = _command_literal(node)
    return mapping


@pytest.mark.asyncio
async def test_every_registered_tool_has_one_explicit_dispatch_command():
    tools = await mcp.list_tools()
    mapping = wrapper_commands()
    assert len(mapping) == 204
    assert {tool.name for tool in tools} == set(mapping) | SPECIAL_MCP_TOOLS
    assert len(set(mapping.values())) == len(mapping)
