"""Live coverage for export-performance and Houdini-to-UE diagnostics."""

from __future__ import annotations

# Third-party
import pytest

pytestmark = pytest.mark.integration


def test_space_checker_proves_y_flip_and_cm_scale(call):
    geo = call("nodes.create_node", parent_path="/obj", node_type="geo", name="space")
    box = call("nodes.create_node", parent_path=geo["node_path"], node_type="box")
    xform = call(
        "nodes.create_node",
        parent_path=geo["node_path"],
        node_type="xform",
        name="ue_space_cm",
    )
    call(
        "nodes.connect_nodes",
        source_path=box["node_path"],
        dest_path=xform["node_path"],
        input_index=0,
    )
    call(
        "parameters.set_parameters",
        node_path=xform["node_path"],
        params={"s": [100.0, -100.0, 100.0]},
    )

    result = call(
        "diagnostics.check_houdini_to_ue_space",
        input_node_path=box["node_path"],
        output_node_path=xform["node_path"],
        scale=100.0,
        tolerance=0.001,
    )
    assert result["bbox_matches"] is True
    assert result["point_mapping"]["matches"] is True
    assert result["axis_mapping_matches"] is True


def test_alembic_profiler_samples_sop_and_restores_frame(call):
    geo = call("nodes.create_node", parent_path="/obj", node_type="geo", name="profile")
    box = call("nodes.create_node", parent_path=geo["node_path"], node_type="box")

    result = call(
        "diagnostics.analyze_alembic_output",
        node_path=box["node_path"],
        start_frame=1,
        end_frame=5,
        sample_count=3,
        fps=60.0,
    )
    assert result["sampled_frames"] == [1.0, 3.0, 5.0]
    assert result["frame_range"]["frame_count"] == 5
    assert result["changing_topology"] is False
    assert all(sample["points"] == 8 for sample in result["samples"])
    assert result["estimated_total_cook_seconds"] >= 0.0
