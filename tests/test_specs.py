"""Tests for the typed composite-tool parameter models.

These models replace the old opaque ``list[dict[str, Any]]`` parameters of
``build_network`` / ``connect_nodes_batch`` so the agent gets a real JSON
Schema. The handlers still consume plain dicts, so the round-trip via
``model_dump`` must reproduce exactly the dict shape they expect.
"""

from __future__ import annotations

# Third-party
import pytest
from pydantic import ValidationError

# Internal
import fxhoudinimcp.tools  # noqa: F401  (registers all tools on import)
from fxhoudinimcp._specs import (
    ConnectionSpec,
    KeyframeSpec,
    NodeSpec,
    SopStepSpec,
    SpareParameterSpec,
)
from fxhoudinimcp.server import mcp


def test_node_spec_model_dump_round_trip():
    spec = NodeSpec(
        type="box",
        name="b1",
        parms={"t": [1, 2, 3], "scale": 2},
        inputs=["a", {"source": "c", "index": 1}],
    )
    dumped = spec.model_dump(exclude_none=True)
    # The handler reads exactly these keys; None fields are dropped, the
    # string input stays a string, and the dict input becomes an InputSpec dict.
    assert dumped == {
        "type": "box",
        "name": "b1",
        "parms": {"t": [1, 2, 3], "scale": 2},
        "inputs": ["a", {"source": "c", "index": 1, "source_output": 0}],
    }


def test_node_spec_forbids_unknown_keys():
    # A mistyped spec key ("parm" instead of "parms") is rejected up front
    # rather than silently ignored by the handler.
    with pytest.raises(ValidationError):
        NodeSpec(type="box", parm={"t": 1})


def test_connection_spec_defaults():
    assert ConnectionSpec(
        source_path="/a", dest_path="/b"
    ).model_dump() == {
        "source_path": "/a",
        "dest_path": "/b",
        "output_index": 0,
        "input_index": 0,
    }


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ConnectionSpec(source_path="/a", dest_path="/b", input_index=-1),
        lambda: NodeSpec(type="box", color=[1.0, 0.0]),
        lambda: NodeSpec(type="box", flags={"dispaly": True}),
        lambda: SpareParameterSpec(
            parm_name="amount", parm_type="unsupported", label="Amount"
        ),
        lambda: SopStepSpec(type="box", unexpected=True),
    ],
)
def test_structural_specs_reject_invalid_agent_arguments(factory):
    with pytest.raises(ValidationError):
        factory()


def test_keyframe_spec_is_explicit_and_serializable():
    assert KeyframeSpec(frame=1, value=2.5).model_dump(exclude_none=True) == {
        "frame": 1.0,
        "value": 2.5,
    }


@pytest.mark.asyncio
async def test_build_network_schema_uses_nodespec():
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["build_network"].inputSchema
    assert schema["properties"]["nodes"]["items"]["$ref"].endswith("/NodeSpec")
    node_def = schema["$defs"]["NodeSpec"]
    # extra="forbid" surfaces as additionalProperties: false, so the agent
    # learns about a mistyped key instead of having it silently ignored.
    assert node_def["additionalProperties"] is False
    assert set(node_def["properties"]) == {
        "type",
        "name",
        "parms",
        "inputs",
        "flags",
        "color",
        "comment",
    }


@pytest.mark.asyncio
async def test_all_composite_tool_schemas_expose_structural_models():
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    composite_definitions = {
        "set_keyframes": "KeyframeSpec",
        "create_spare_parameters": "SpareParameterSpec",
        "build_sop_chain": "SopStepSpec",
    }
    for tool_name, definition_name in composite_definitions.items():
        schema = tools[tool_name].inputSchema
        property_name = "keyframes" if tool_name == "set_keyframes" else (
            "parameters" if tool_name == "create_spare_parameters" else "steps"
        )
        property_schema = schema["properties"][property_name]
        array_schema = next(
            option for option in property_schema.get("anyOf", [property_schema])
            if option.get("type") == "array"
        )
        assert array_schema["items"]["$ref"].endswith(
            f"/{definition_name}"
        )
        assert schema["$defs"][definition_name]["additionalProperties"] is False
