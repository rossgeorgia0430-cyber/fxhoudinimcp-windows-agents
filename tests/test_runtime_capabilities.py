"""Unit tests for safe Houdini runtime capability discovery."""

from __future__ import annotations

# Built-in
import sys
from pathlib import Path

# Third-party
import pytest

HOUDINI_PYTHON = (
    Path(__file__).resolve().parents[1] / "houdini" / "scripts" / "python"
)
sys.path.insert(0, str(HOUDINI_PYTHON))

from fxhoudinimcp_server import runtime_capabilities  # noqa: E402


class _Category:
    def __init__(self, *names: str):
        self._names = names

    def nodeTypes(self):
        return {name: object() for name in self._names}


class _BrokenCategory:
    def nodeTypes(self):
        raise RuntimeError("optional plugin is unavailable")


class _License:
    def name(self):
        return "Commercial"


class _Hou:
    def __init__(self, categories, hfs: Path, ui: bool = True):
        self._categories = categories
        self._hfs = hfs
        self._ui = ui

    def applicationVersionString(self):
        return "99.0.0-test"

    def applicationVersion(self):
        return (99, 0, 0)

    def isUIAvailable(self):
        return self._ui

    def licenseCategory(self):
        return _License()

    def nodeTypeCategories(self):
        return self._categories

    def expandString(self, value):
        assert value == "$HFS"
        return str(self._hfs)


@pytest.fixture
def complete_hou(tmp_path):
    help_dir = tmp_path / "houdini" / "help"
    help_dir.mkdir(parents=True)
    for name in ("nodes.zip", "hom.zip", "vex.zip", "expressions.zip"):
        (help_dir / name).touch()
    return _Hou(
        {
            "Object": _Category("geo"),
            "Sop": _Category("box", "vellumsolver"),
            "Dop": _Category("pyrosolver"),
            "Lop": _Category("karmarendersettings", "sublayer"),
            "Top": _Category("geometryimport"),
            "Cop": _Category("copnet"),
            "Chop": _Category("wave"),
            "Driver": _Category("karma", "ifd"),
            "Vop": _Category("principledshader"),
        },
        tmp_path,
    )


def _commands():
    return [
        "scene.get_scene_info",
        "nodes.create_node",
        "parameters.get_parameter",
        "geometry.get_geometry_info",
        "dops.get_simulation_info",
        "lops.get_stage_info",
        "rendering.start_render",
        "tops.get_top_network_info",
        "cops.get_cop_info",
        "animation.get_frame",
        "viewport.capture_screenshot",
        "help.search_help",
    ]


def test_complete_runtime_reports_actionable_capabilities(
    complete_hou,
    monkeypatch,
):
    monkeypatch.setattr(
        runtime_capabilities.importlib.util,
        "find_spec",
        lambda name: object() if name == "pxr" else None,
    )
    result = runtime_capabilities.collect_runtime_capabilities(
        complete_hou,
        commands=_commands(),
        handler_status={
            "handler_modules_total": 22,
            "handler_modules_loaded": ["scene_handlers", "node_handlers"],
            "handler_modules_failed": [],
            "handler_module_failures": [],
        },
        environ={},
    )

    assert result["runtime_status"] == "ready"
    assert result["houdini"]["version"] == "99.0.0-test"
    assert result["houdini"]["ui_available"] is True
    assert result["license"]["category"] == "Commercial"
    assert result["license"]["renderer_license_verified"] is False
    assert result["node_categories"]["sop"]["node_type_count"] == 2
    assert result["subsystems"]["simulation"]["available"] is True
    assert result["subsystems"]["usd"]["available"] is True
    assert result["subsystems"]["viewport"]["available"] is True
    assert result["help"]["available"] is True
    assert result["renderers"]["installed"] == ["karma", "mantra"]
    assert result["renderers"]["license_verified"] is False
    assert result["probe_errors"] == []


def test_optional_failures_degrade_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_capabilities.importlib.util, "find_spec", lambda name: None)
    hou = _Hou(
        {
            "Sop": _Category("box"),
            "Dop": _BrokenCategory(),
        },
        tmp_path,
        ui=False,
    )
    result = runtime_capabilities.collect_runtime_capabilities(
        hou,
        commands=["scene.get_scene_info", "nodes.create_node"],
        handler_status={
            "handler_modules_total": 22,
            "handler_modules_loaded": ["scene_handlers"],
            "handler_modules_failed": ["lops_handlers"],
            "handler_module_failures": [
                {
                    "module": "lops_handlers",
                    "type": "ImportError",
                    "message": "pxr unavailable",
                }
            ],
        },
        environ={},
    )

    assert result["runtime_status"] == "degraded"
    assert result["handlers"]["failures"][0]["module"] == "lops_handlers"
    assert result["subsystems"]["simulation"]["available"] is False
    assert result["subsystems"]["usd"]["available"] is False
    assert result["subsystems"]["viewport"]["available"] is False
    assert result["help"]["available"] is False
    assert any(error["probe"] == "node_types.dop" for error in result["probe_errors"])
    assert all("traceback" not in error for error in result["probe_errors"])
