"""Tests for MCP tool wrappers: validate bridge delegation."""

from __future__ import annotations

# Third-party
import pytest

# Internal
from fxhoudinimcp.errors import ConnectionError as HoudiniConnectionError
from fxhoudinimcp.tools.code import execute_python
from fxhoudinimcp.tools.materials import list_materials
from fxhoudinimcp.tools.nodes import create_node
from fxhoudinimcp.tools.scene import (
    get_houdini_connection_status,
    get_scene_info,
    new_scene,
)
from fxhoudinimcp.tools.diagnostics import (
    analyze_alembic_output,
    check_houdini_to_ue_space,
)
from fxhoudinimcp.tools.viewport import flipbook
from fxhoudinimcp.tools.workflows import setup_pyro_sim


class TestSceneTools:
    @pytest.mark.asyncio
    async def test_get_scene_info(self, mock_ctx, mock_bridge):
        mock_bridge.execute.return_value = {"hip_file": "/tmp/test.hip"}
        result = await get_scene_info(mock_ctx)
        mock_bridge.execute.assert_called_once_with("scene.get_scene_info")
        assert result == {"hip_file": "/tmp/test.hip"}

    @pytest.mark.asyncio
    async def test_new_scene(self, mock_ctx, mock_bridge):
        mock_bridge.execute.return_value = {"created": True}
        await new_scene(mock_ctx, save_current=True)
        mock_bridge.execute.assert_called_once_with("scene.new_scene", {"save_current": True})

    @pytest.mark.asyncio
    async def test_connection_status_success(self, mock_ctx, mock_bridge):
        mock_bridge.base_url = "http://localhost:8100"
        mock_bridge.health_check.return_value = {"status": "ok", "pid": 123}
        result = await get_houdini_connection_status(mock_ctx)
        assert result["connected"] is True
        assert result["base_url"] == "http://localhost:8100"
        assert result["health"] == {"status": "ok", "pid": 123}
        assert result["mcp_tool_profile"]["name"] == "full"

    @pytest.mark.asyncio
    async def test_connection_status_disconnect(self, mock_ctx, mock_bridge):
        mock_bridge.base_url = "http://localhost:8100"
        mock_bridge.health_check.side_effect = HoudiniConnectionError(
            "Cannot connect",
            details={"url": "http://localhost:8100"},
        )
        result = await get_houdini_connection_status(mock_ctx)
        assert result["connected"] is False
        assert result["base_url"] == "http://localhost:8100"
        assert result["mcp_tool_profile"]["name"] == "full"
        assert result["details"] == {"url": "http://localhost:8100"}


class TestNodeTools:
    @pytest.mark.asyncio
    async def test_create_node_required_params(self, mock_ctx, mock_bridge):
        mock_bridge.execute.return_value = {"path": "/obj/geo1/box1"}
        await create_node(mock_ctx, parent_path="/obj/geo1", node_type="box")
        mock_bridge.execute.assert_called_once_with(
            "nodes.create_node",
            {"parent_path": "/obj/geo1", "node_type": "box"},
        )

    @pytest.mark.asyncio
    async def test_create_node_all_params(self, mock_ctx, mock_bridge):
        await create_node(
            mock_ctx,
            parent_path="/obj",
            node_type="geo",
            name="my_geo",
            position=[0, 0],
        )
        mock_bridge.execute.assert_called_once_with(
            "nodes.create_node",
            {"parent_path": "/obj", "node_type": "geo", "name": "my_geo", "position": [0, 0]},
        )


class TestCodeTools:
    @pytest.mark.asyncio
    async def test_execute_python_code_only(self, mock_ctx, mock_bridge):
        result = await execute_python(
            mock_ctx,
            code="print('hi')",
            justification="no dedicated tool prints to the console",
        )
        mock_bridge.execute.assert_called_once_with(
            "code.execute_python",
            {"code": "print('hi')"},
        )
        # The justification is echoed back, never forwarded to Houdini.
        assert result["justification"]

    @pytest.mark.asyncio
    async def test_execute_python_with_return(self, mock_ctx, mock_bridge):
        await execute_python(
            mock_ctx,
            code="x = 1 + 1",
            justification="no dedicated tool evaluates arbitrary Python",
            return_expression="x",
        )
        mock_bridge.execute.assert_called_once_with(
            "code.execute_python",
            {"code": "x = 1 + 1", "return_expression": "x"},
        )

    @pytest.mark.asyncio
    async def test_justification_required_in_schemas(self):
        """The schema must force clients to articulate why VEX/Python."""
        from fxhoudinimcp.server import mcp

        tools = {t.name: t for t in await mcp.list_tools()}
        for tool_name in ("execute_python", "create_wrangle"):
            schema = tools[tool_name].inputSchema
            assert "justification" in schema["required"], (
                f"{tool_name} must require a justification"
            )


class TestWorkflowTools:
    @pytest.mark.asyncio
    async def test_setup_pyro_defaults(self, mock_ctx, mock_bridge):
        await setup_pyro_sim(mock_ctx)
        mock_bridge.execute.assert_called_once_with(
            "workflow.setup_pyro_sim",
            {
                "source_geo": "/obj/geo1/sphere1",
                "container": "box",
                "res_scale": 1.0,
                "substeps": 1,
                "name": "pyro_sim",
            },
        )


class TestMaterialTools:
    @pytest.mark.asyncio
    async def test_list_materials_default(self, mock_ctx, mock_bridge):
        await list_materials(mock_ctx)
        mock_bridge.execute.assert_called_once_with(
            "materials.list_materials",
            {"root_path": "/mat"},
        )


class TestViewportTools:
    @pytest.mark.asyncio
    async def test_flipbook_defaults_to_mplay(self, mock_ctx, mock_bridge):
        # No output_path / range → only the always-sent frame_increment is
        # forwarded; the handler resolves range + MPlay defaults itself.
        await flipbook(mock_ctx)
        mock_bridge.execute.assert_called_once_with(
            "viewport.flipbook",
            {"frame_increment": 1},
        )

    @pytest.mark.asyncio
    async def test_flipbook_forwards_all_params(self, mock_ctx, mock_bridge):
        await flipbook(
            mock_ctx,
            output_path="$HIP/fb/preview.$F4.jpg",
            start_frame=1,
            end_frame=668,
            camera_path="/obj/cam_shot46",
            resolution=[1280, 720],
            frame_increment=2,
            open_in_mplay=False,
            pane_name="persp1",
        )
        mock_bridge.execute.assert_called_once_with(
            "viewport.flipbook",
            {
                "frame_increment": 2,
                "output_path": "$HIP/fb/preview.$F4.jpg",
                "start_frame": 1,
                "end_frame": 668,
                "camera_path": "/obj/cam_shot46",
                "resolution": [1280, 720],
                "open_in_mplay": False,
                "pane_name": "persp1",
            },
        )


class TestDiagnosticsTools:
    @pytest.mark.asyncio
    async def test_analyze_alembic_output_forwards_optional_values(
        self, mock_ctx, mock_bridge
    ):
        await analyze_alembic_output(
            mock_ctx,
            "/obj/geo/OUT_ABC",
            output_sop_path="/obj/geo/OUT_MESH",
            start_frame=174,
            end_frame=701,
            frame_step=1,
            sample_count=6,
            fps=60,
        )
        mock_bridge.execute.assert_called_once_with(
            "diagnostics.analyze_alembic_output",
            {
                "node_path": "/obj/geo/OUT_ABC",
                "output_sop_path": "/obj/geo/OUT_MESH",
                "start_frame": 174,
                "end_frame": 701,
                "frame_step": 1,
                "sample_count": 6,
                "fps": 60,
            },
        )

    @pytest.mark.asyncio
    async def test_check_houdini_to_ue_space_forwards_contract(
        self, mock_ctx, mock_bridge
    ):
        await check_houdini_to_ue_space(
            mock_ctx,
            "/obj/geo/BEFORE_UE",
            "/obj/geo/AFTER_UE",
            scale=100,
            tolerance=0.01,
            max_point_samples=4096,
        )
        mock_bridge.execute.assert_called_once_with(
            "diagnostics.check_houdini_to_ue_space",
            {
                "input_node_path": "/obj/geo/BEFORE_UE",
                "output_node_path": "/obj/geo/AFTER_UE",
                "scale": 100,
                "tolerance": 0.01,
                "max_point_samples": 4096,
            },
        )
