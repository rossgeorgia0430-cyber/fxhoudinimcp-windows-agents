"""Unit tests for Houdini-side Alembic diagnostics with a minimal fake hou."""

from __future__ import annotations

# Built-in
import importlib.util
import sys
import types
from pathlib import Path

# Third-party
import pytest


class _Attrib:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class _BBox:
    def __init__(self, minimum, maximum):
        self.minimum = minimum
        self.maximum = maximum

    def minvec(self):
        return self.minimum

    def maxvec(self):
        return self.maximum

    def sizevec(self):
        return tuple(b - a for a, b in zip(self.minimum, self.maximum, strict=True))

    def center(self):
        return tuple((a + b) * 0.5 for a, b in zip(self.minimum, self.maximum, strict=True))


class _Point:
    def __init__(self, position):
        self._position = position

    def position(self):
        return self._position


class _Geometry:
    def __init__(self, points, prims=1, vertices=3, attrs=None):
        self._points = points
        self._prims = prims
        self._vertices = vertices
        self._attrs = attrs or {"point": ["P"], "vertex": [], "prim": []}

    def intrinsicValue(self, name):
        return {
            "pointcount": len(self._points),
            "primitivecount": self._prims,
            "vertexcount": self._vertices,
        }[name]

    def boundingBox(self):
        mins = tuple(min(p[i] for p in self._points) for i in range(3))
        maxs = tuple(max(p[i] for p in self._points) for i in range(3))
        return _BBox(mins, maxs)

    def pointAttribs(self):
        return [_Attrib(name) for name in self._attrs["point"]]

    def vertexAttribs(self):
        return [_Attrib(name) for name in self._attrs["vertex"]]

    def primAttribs(self):
        return [_Attrib(name) for name in self._attrs["prim"]]

    def point(self, index):
        return _Point(self._points[index])


class _Parm:
    def __init__(self, value):
        self.value = value

    def eval(self):
        return self.value

    def evalAsString(self):
        return str(self.value)


class _Node:
    def __init__(self, hou_module, path, geometry_by_frame=None, parms=None):
        self.hou = hou_module
        self._path = path
        self.geometry_by_frame = geometry_by_frame or {}
        self.parms = parms or {}

    def path(self):
        return self._path

    def geometry(self):
        if not self.geometry_by_frame:
            return None
        frame = int(self.hou.frame())
        return self.geometry_by_frame.get(frame, next(iter(self.geometry_by_frame.values())))

    def parm(self, name):
        value = self.parms.get(name)
        return None if value is None else _Parm(value)

    def inputs(self):
        return ()


@pytest.fixture
def handler_module(monkeypatch):
    registered = {}
    hou = types.ModuleType("hou")
    hou.OperationFailed = type("OperationFailed", (Exception,), {})
    hou.Node = object
    hou.Geometry = object
    hou._frame = 9.0
    hou._nodes = {}
    hou.node = lambda path: hou._nodes.get(path)
    hou.frame = lambda: hou._frame
    hou.setFrame = lambda frame: setattr(hou, "_frame", float(frame))
    hou.fps = lambda: 60.0
    hou.playbar = types.SimpleNamespace(frameRange=lambda: (1.0, 4.0))

    dispatcher = types.ModuleType("fxhoudinimcp_server.dispatcher")
    dispatcher.register_handler = lambda name, fn: registered.__setitem__(name, fn)
    package = types.ModuleType("fxhoudinimcp_server")
    package.__path__ = []

    monkeypatch.setitem(sys.modules, "hou", hou)
    monkeypatch.setitem(sys.modules, "fxhoudinimcp_server", package)
    monkeypatch.setitem(sys.modules, "fxhoudinimcp_server.dispatcher", dispatcher)

    path = (
        Path(__file__).parents[1]
        / "houdini/scripts/python/fxhoudinimcp_server/handlers/alembic_diagnostics_handlers.py"
    )
    spec = importlib.util.spec_from_file_location("_alembic_diagnostics_handlers_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module._test_hou = hou
    module._registered = registered
    return module


def test_analyze_alembic_output_profiles_samples_and_restores_frame(handler_module):
    hou = handler_module._test_hou
    geometries = {
        1: _Geometry([(0, 0, 0), (1, 1, 1)], attrs={"point": ["P", "v"], "vertex": ["N"], "prim": ["name"]}),
        2: _Geometry([(0, 0, 0), (2, 1, 1)]),
        3: _Geometry([(0, 0, 0), (3, 1, 1), (4, 2, 1)], prims=2, vertices=6),
        4: _Geometry([(0, 0, 0), (4, 1, 1), (5, 2, 1)], prims=2, vertices=6),
    }
    hou._nodes["/obj/out"] = _Node(hou, "/obj/out", geometries)

    result = handler_module.analyze_alembic_output(
        node_path="/obj/out", start_frame=1, end_frame=4, sample_count=3
    )

    assert result["frame_range"]["frame_count"] == 4
    assert result["sampled_frames"] == [1.0, 3.0, 4.0]
    assert result["changing_topology"] is True
    assert result["samples"][0]["attributes"] == {
        "point": ["P", "v"], "vertex": ["N"], "prim": ["name"]
    }
    assert result["estimated_total_cook_seconds"] >= 0
    assert hou.frame() == 9.0


def test_analyze_alembic_output_uses_rop_range_and_explicit_sop(handler_module):
    hou = handler_module._test_hou
    hou._nodes["/obj/out"] = _Node(hou, "/obj/out", {10: _Geometry([(0, 0, 0)])})
    hou._nodes["/out/abc"] = _Node(
        hou, "/out/abc", parms={"trange": 1, "f1": 10, "f2": 14, "f3": 2}
    )

    result = handler_module.analyze_alembic_output(
        node_path="/out/abc", output_sop_path="/obj/out", sample_count=None
    )

    assert result["frame_range"] == {
        "start": 10.0, "end": 14.0, "step": 2.0, "frame_count": 3, "source": "rop"
    }
    assert result["fps"] == 60.0


def test_check_houdini_to_ue_space_validates_bbox_and_points(handler_module):
    hou = handler_module._test_hou
    source = [(-1, 2, 3), (4, -5, 6), (0, 1, -2)]
    converted = [(x * 100, -y * 100, z * 100) for x, y, z in source]
    hou._nodes["/obj/in"] = _Node(hou, "/obj/in", {9: _Geometry(source)})
    hou._nodes["/obj/out"] = _Node(hou, "/obj/out", {9: _Geometry(converted)})

    result = handler_module.check_houdini_to_ue_space(
        input_node_path="/obj/in", output_node_path="/obj/out", scale=100
    )

    assert result["bbox_matches"] is True
    assert result["point_mapping"]["matches"] is True
    assert result["axis_mapping_matches"] is True
    assert result["expected_output_bbox"] == result["actual_output_bbox"]


def test_check_houdini_to_ue_space_detects_missing_y_flip(handler_module):
    hou = handler_module._test_hou
    source = [(1, 2, 3), (4, 5, 6)]
    wrong = [(x * 100, y * 100, z * 100) for x, y, z in source]
    hou._nodes["/obj/in"] = _Node(hou, "/obj/in", {9: _Geometry(source)})
    hou._nodes["/obj/out"] = _Node(hou, "/obj/out", {9: _Geometry(wrong)})

    result = handler_module.check_houdini_to_ue_space(
        input_node_path="/obj/in", output_node_path="/obj/out", scale=100
    )

    assert result["bbox_matches"] is False
    assert result["point_mapping"]["failures"] == 2
    assert result["axis_mapping_matches"] is False


def test_handlers_register_expected_commands(handler_module):
    assert set(handler_module._registered) == {
        "diagnostics.analyze_alembic_output",
        "diagnostics.check_houdini_to_ue_space",
    }
