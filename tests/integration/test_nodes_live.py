"""Live node-handler tests: verify returned claims against actual scene state."""

from __future__ import annotations

# Third-party
import hou
import pytest

pytestmark = pytest.mark.integration


def _make_geo(call, name: str = "geo1") -> str:
    data = call("nodes.create_node", parent_path="/obj", node_type="geo", name=name)
    return data["node_path"]


class TestCreateDelete:
    def test_create_node_exists_with_claimed_type(self, call):
        geo = _make_geo(call)
        box = call("nodes.create_node", parent_path=geo, node_type="box")
        node = hou.node(box["node_path"])
        assert node is not None
        assert node.type().name() == "box"
        assert box["node_type"] == "box"

    def test_create_node_honors_position(self, call):
        geo = _make_geo(call)
        box = call(
            "nodes.create_node",
            parent_path=geo,
            node_type="box",
            position=[4.0, -2.0],
        )
        assert tuple(hou.node(box["node_path"]).position()) == (4.0, -2.0)

    def test_create_node_unknown_type_is_clean_error(self, call):
        error = call(
            "nodes.create_node",
            parent_path="/obj",
            node_type="definitely_not_a_real_node",
            expect_error=True,
        )
        assert "definitely_not_a_real_node" in error["message"]

    def test_delete_node_removes_it(self, call):
        _make_geo(call, name="doomed")
        call("nodes.delete_node", node_path="/obj/doomed")
        assert hou.node("/obj/doomed") is None

    def test_rename_node(self, call):
        _make_geo(call, name="before")
        call("nodes.rename_node", node_path="/obj/before", new_name="after")
        assert hou.node("/obj/before") is None
        assert hou.node("/obj/after") is not None

    def test_copy_node_creates_independent_copy(self, call):
        geo = _make_geo(call)
        box = call("nodes.create_node", parent_path=geo, node_type="box")
        copy = call("nodes.copy_node", node_path=box["node_path"])
        copied = hou.node(copy["copied_path"])
        assert copied is not None
        assert copied.path() != box["node_path"]
        assert copied.type().name() == "box"


class TestWiring:
    def test_connect_nodes_wires_claimed_inputs(self, call):
        geo = _make_geo(call)
        a = call("nodes.create_node", parent_path=geo, node_type="box")["node_path"]
        b = call("nodes.create_node", parent_path=geo, node_type="sphere")["node_path"]
        m = call("nodes.create_node", parent_path=geo, node_type="merge")["node_path"]
        call("nodes.connect_nodes", source_path=a, dest_path=m, input_index=0)
        call("nodes.connect_nodes", source_path=b, dest_path=m, input_index=1)
        assert [n.path() for n in hou.node(m).inputs()] == [a, b]

    def test_disconnect_node_removes_input(self, call):
        geo = _make_geo(call)
        a = call("nodes.create_node", parent_path=geo, node_type="box")["node_path"]
        x = call("nodes.create_node", parent_path=geo, node_type="xform")["node_path"]
        call("nodes.connect_nodes", source_path=a, dest_path=x)
        call("nodes.disconnect_node", node_path=x, input_index=0)
        assert hou.node(x).inputs() == ()

    def test_set_node_flags_bypass_is_real(self, call):
        geo = _make_geo(call)
        box = call("nodes.create_node", parent_path=geo, node_type="box")["node_path"]
        call("nodes.set_node_flags", node_path=box, bypass=True)
        assert hou.node(box).isBypassed() is True

    def test_set_node_position_is_applied(self, call):
        geo = _make_geo(call)
        box = call("nodes.create_node", parent_path=geo, node_type="box")["node_path"]
        call("nodes.set_node_position", node_path=box, x=1.5, y=-3.0)
        assert tuple(hou.node(box).position()) == (1.5, -3.0)


class TestDiscovery:
    def test_find_nodes_returns_only_existing_paths(self, call):
        geo = _make_geo(call)
        call("nodes.create_node", parent_path=geo, node_type="box", name="findme1")
        call("nodes.create_node", parent_path=geo, node_type="box", name="findme2")
        data = call("nodes.find_nodes", pattern="findme*")
        assert data["count"] == 2
        for summary in data["nodes"]:
            assert hou.node(summary["path"]) is not None

    def test_list_node_types_names_are_instantiable(self, call):
        data = call("nodes.list_node_types", context="Sop", filter="scatter")
        names = [t["name"] for t in data["types"]]
        assert any(n.startswith("scatter") for n in names)
        category = hou.nodeTypeCategories()["Sop"]
        for name in names:
            assert name in category.nodeTypes(), f"claimed type {name} does not exist"

    def test_get_node_info_matches_reality(self, call):
        geo = _make_geo(call, name="infogeo")
        data = call("nodes.get_node_info", node_path=geo)
        assert data["name"] == "infogeo"
        assert data["node_path"] == "/obj/infogeo"
        assert data["type"]["name"] == "geo"

    def test_list_children_counts_match(self, call):
        geo = _make_geo(call)
        for _ in range(3):
            call("nodes.create_node", parent_path=geo, node_type="box")
        data = call("nodes.list_children", parent_path=geo)
        assert len(hou.node(geo).children()) == 3
        children = data.get("children", data.get("nodes", []))
        assert len(children) == 3
