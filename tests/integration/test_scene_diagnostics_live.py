"""Live coverage for mesh, motion, FBX/HIP, contact-sheet, and flipbook-path diagnostics."""

from __future__ import annotations

# Built-in
import os

# Third-party
import hou
import pytest

# Internal
from fxhoudinimcp_server.handlers.viewport_handlers import (
    _expand_keeping_frame_tokens,
    _missing_flipbook_files,
)

pytestmark = pytest.mark.integration


def _sop(parent: hou.Node, node_type: str, source: hou.Node | None = None, **parms) -> hou.Node:
    node = parent.createNode(node_type)
    if source is not None:
        node.setInput(0, source)
    for name, value in parms.items():
        if isinstance(value, tuple):
            node.parmTuple(name).set(value)
        else:
            node.parm(name).set(value)
    return node


def _wrangle(parent: hou.Node, source: hou.Node, snippet: str, run_over: str = "point") -> hou.Node:
    node = _sop(parent, "attribwrangle", source, snippet=snippet)
    node.parm("class").set(run_over)
    return node


@pytest.fixture
def geo() -> hou.Node:
    return hou.node("/obj").createNode("geo", "diag")


class TestMeshDiagnostics:
    def test_closed_box_volume_and_open_grid_edges(self, call, geo):
        box = _sop(geo, "box")
        result = call("diagnostics.mesh_topology_report", node_path=box.path())
        assert result["closed"] is True
        assert result["components"] == 1
        assert result["volume"] == pytest.approx(1.0)
        assert result["normals_outward"] is True

        grid = _sop(geo, "grid", rows=3, cols=3)
        result = call("diagnostics.mesh_topology_report", node_path=grid.path())
        assert result["closed"] is False
        assert result["boundary_edges"] == 8
        assert result["volume"] is None

    def test_pieces_with_open_curves_or_several_parts_are_listed(self, call, geo):
        whole = _wrangle(geo, _sop(geo, "box"), 's@name = "whole";', "primitive")
        split = _wrangle(
            geo,
            _sop(geo, "merge"),
            's@name = "split";',
            "primitive",
        )
        merge_parts = split.input(0)
        merge_parts.setInput(0, _sop(geo, "box", t=(3, 0, 0)))
        merge_parts.setInput(1, _sop(geo, "box", t=(5, 0, 0)))
        merge_parts.setInput(2, _sop(geo, "line"))
        merged = _sop(geo, "merge")
        merged.setInput(0, whole)
        merged.setInput(1, split)

        result = call("diagnostics.mesh_topology_report", node_path=merged.path(), piece_attrib="name")
        assert result["open_curves"] == 1
        pieces = result["pieces"]
        assert pieces["count"] == 2
        assert [row["piece"] for row in pieces["problems"]] == ["split"]
        assert pieces["problems"][0]["components"] == 2
        assert pieces["problems"][0]["open_curves"] == 1
        assert pieces["closed_volume"]["total"] == pytest.approx(3.0)

    def test_uv_report_finds_collapsed_and_flipped_faces(self, call, geo):
        # Planar XZ projection: the four side faces collapse to lines, and the
        # top and bottom faces are wound in opposite directions.
        uvs = _wrangle(geo, _sop(geo, "box"), "v@uv = set(@P.x, @P.z, 0);", "vertex")
        result = call("diagnostics.uv_quality_report", node_path=uvs.path())
        report = result["groups"][0]
        assert report["polygons"] == 6
        assert report["degenerate_uv"] == 4
        assert report["flipped_uv"] == 1
        assert report["texel_scale"]["p50"] == pytest.approx(1.0)

    def test_ray_grid_reports_hits_and_coverage_holes(self, call, geo):
        grid = _sop(geo, "grid", sizex=10, sizey=10)
        result = call(
            "diagnostics.ray_intersect",
            node_path=grid.path(),
            grid_center=[0, 5, 0],
            grid_size=20,
            grid_resolution=4,
        )
        assert result["ray_count"] == 16
        assert result["hits"] == 4
        assert result["misses"] == 12
        assert result["distance"]["min"] == pytest.approx(5.0)

        too_short = call(
            "diagnostics.ray_intersect",
            node_path=grid.path(),
            origins=[[0, 5, 0]],
            max_distance=4.0,
        )
        assert too_short["hits"] == 0


class TestMotionDiagnostics:
    def test_named_prims_are_tracked_with_bounds(self, call, geo):
        named = _wrangle(geo, _sop(geo, "box"), 's@name = "crate";', "primitive")
        moving = _sop(geo, "xform", named)
        moving.parm("tx").setExpression("$F")
        original_frame = hou.frame()

        result = call(
            "diagnostics.track_named_elements",
            node_path=moving.path(),
            frames=[1, 3],
            element_class="prim",
            attribs=[],
            include_bounds=True,
        )
        rows = result["samples"]["crate"]
        assert [row["count"] for row in rows] == [6, 6]
        assert rows[1]["bounds"]["center"][0] - rows[0]["bounds"]["center"][0] == pytest.approx(2.0)
        assert hou.frame() == original_frame

    def test_rbd_stats_from_position_differences(self, call, geo):
        # Two named points fall one unit per frame and land on y=0 at frame 10.
        points = _sop(geo, "line", points=2, dist=3.0)
        points.parmTuple("dir").set((1, 0, 0))
        falling = _wrangle(geo, points, 's@name = itoa(@ptnum); @P.y = max(0, 10 - @Frame);')

        result = call(
            "diagnostics.rbd_motion_stats",
            node_path=falling.path(),
            start_frame=1,
            end_frame=15,
            sample_frames=[5],
            center=[0, 0, 0],
        )
        assert result["velocity_source"] == "finite difference of P"
        assert result["samples"][0]["speed_p50"] == pytest.approx(hou.fps())
        assert result["samples"][0]["moving"] == 2
        assert result["settle_frame"] == 11
        assert result["lowest"]["height"] == 0.0
        assert result["rebound"]["pieces"] == 0
        assert result["farthest"][0]["name"] == "1"

    def test_reset_requires_a_solver(self, call, geo):
        box = _sop(geo, "box")
        error = call(
            "diagnostics.rbd_motion_stats",
            node_path=box.path(),
            start_frame=1,
            end_frame=2,
            reset_node_path=box.path(),
            expect_error=True,
        )
        assert "reset button" in error["message"]


class TestFileInspection:
    def test_fbx_round_trip_flags_polyline_pieces(self, call, geo, tmp_path):
        # "solid" is a closed box; "wired" also carries an open polyline, which
        # the FBX ROP splits into its own node that takes the animation.
        solid = _wrangle(geo, _sop(geo, "box"), 's@name = "solid";', "primitive")
        wired_parts = _sop(geo, "merge")
        wired_parts.setInput(0, _sop(geo, "box", t=(3, 0, 0)))
        wired_parts.setInput(1, _sop(geo, "line"))
        wired = _wrangle(geo, wired_parts, 's@name = "wired";', "primitive")
        pieces = _sop(geo, "merge")
        pieces.setInput(0, solid)
        pieces.setInput(1, wired)
        packed = _sop(geo, "assemble", pieces, pieceattrib="name", newname=0, pack_geo=1)
        moving = _sop(geo, "xform", packed)
        moving.parm("ty").setExpression("$F * 0.1")
        # Assemble stores each piece's name on the packed point.
        with_path = _wrangle(
            geo, moving, 's@path = "root/" + point(0, "name", primpoint(0, @primnum, 0));', "primitive"
        )

        fbx_path = str(tmp_path / "pieces.fbx").replace("\\", "/")
        rop = _sop(geo, "rop_fbx", with_path, sopoutput=fbx_path, buildfrompath=1)
        rop.parm("trange").set(1)
        rop.parm("f1").deleteAllKeyframes()
        rop.parm("f2").deleteAllKeyframes()
        rop.parmTuple("f").set((1, 10, 1))
        rop.render()
        assert os.path.isfile(fbx_path)

        unpacked = _sop(geo, "unpack", moving)
        closed_only = _sop(geo, "blast", unpacked, group="@intrinsic:closed==0", grouptype="prims")
        result = call(
            "diagnostics.inspect_fbx",
            file_path=fbx_path,
            compare_sop_path=closed_only.path(),
            frames=[1, 10],
        )
        assert result["key_range"] == [1.0, 10.0]
        assert result["curve_only_geo_nodes"]
        assert "wired" in result["static_geo_nodes"]
        comparison = result["comparison"]
        assert comparison["matched"] == 2
        assert comparison["frames"][1]["worst_piece"] == "wired"
        assert comparison["frames"][1]["max_error"] > 0.5
        assert not [node for node in hou.node("/obj").children() if node.name().endswith("_fbx")]

    def test_hip_file_is_read_in_a_separate_process(self, call, geo, tmp_path):
        _wrangle(geo, _sop(geo, "box"), "@P.y += 1; // marker")
        hip_path = str(tmp_path / "reference.hip").replace("\\", "/")
        hou.hipFile.save(hip_path)
        hou.hipFile.clear(suppress_save_prompt=True)

        result = call("diagnostics.inspect_hip_file", hip_path=hip_path, root_path="/obj/diag")
        assert result["root_path"] == "/obj/diag"
        wrangles = [node for node in result["nodes"] if node["type"] == "Sop/attribwrangle"]
        assert wrangles[0]["parms"]["snippet"]["value"] == "@P.y += 1; // marker"
        boxes = [node for node in result["nodes"] if node["type"] == "Sop/box"]
        assert boxes[0]["parms"] == {}


class TestImagesAndFlipbookPaths:
    def test_contact_sheet_tiles_row_major(self, call, tmp_path):
        import OpenImageIO as oiio

        paths = []
        for index, value in enumerate((0.2, 0.5, 0.8)):
            buf = oiio.ImageBuf(oiio.ImageSpec(40, 20, 3, oiio.UINT8))
            oiio.ImageBufAlgo.fill(buf, (value, value, value))
            path = str(tmp_path / f"frame{index}.png").replace("\\", "/")
            buf.write(path)
            paths.append(path)

        sheet_path = str(tmp_path / "sheet.png").replace("\\", "/")
        result = call("image.make_contact_sheet", images=paths, output_path=sheet_path, columns=2, tile_width=20)
        assert result["rows"] == 2
        assert result["tile_size"] == [20, 10]
        sheet = oiio.ImageBuf(sheet_path)
        assert (sheet.spec().width, sheet.spec().height) == (40, 20)
        assert sheet.getpixel(25, 2)[0] == pytest.approx(0.5, abs=0.01)
        assert sheet.getpixel(25, 12)[0] == 0.0

    def test_flipbook_paths_keep_frame_tokens(self, tmp_path):
        pattern = _expand_keeping_frame_tokens("$HIP/p.$F4.${F3}.$FF.$FSTART.jpg")
        assert pattern == hou.expandString("$HIP") + "/p.$F4.${F3}.$FF." + hou.expandString("$FSTART") + ".jpg"

        sequence = str(tmp_path / "s.$F4.jpg").replace("\\", "/")
        for frame in (1, 3):
            open(hou.expandStringAtFrame(sequence, frame), "w").close()
        assert _missing_flipbook_files(sequence, 1, 5, 2) == [5]
