"""Name-keyed motion diagnostics across frames.

Frames are cooked in the order given so solver networks advance
sequentially, and the caller's frame is restored before returning, including
when cooking fails.
"""

from __future__ import annotations

# Built-in
import math
import time
from dataclasses import dataclass, field
from typing import Any

# Third-party
import hou

# Internal
from fxhoudinimcp_server.dispatcher import register_handler
from fxhoudinimcp_server.handlers.alembic_diagnostics_handlers import (
    _all_frames,
    _node_or_raise,
    _sample_frames,
)
from fxhoudinimcp_server.handlers.diagnostics_handlers import (
    _percentile,
    _read_flat_values_by_class,
    _rounded,
)

# Reset buttons of the RBD Bullet Solver SOP and of a DOP Network.
_RESET_BUTTONS = ("resetsim", "resimulate")
_UP_AXES = {"x": 0, "y": 1, "z": 2}
_DEFAULT_SAMPLE_COUNT = 8


def _element_names(geo: hou.Geometry, element_class: str, name_attrib: str) -> list[str]:
    """Read a string or integer name attribute for every point or primitive."""
    is_point = element_class == "point"
    attrib = geo.findPointAttrib(name_attrib) if is_point else geo.findPrimAttrib(name_attrib)
    if attrib is None:
        raise hou.OperationFailed(f"{element_class} attribute not found: {name_attrib}")
    if attrib.dataType() == hou.attribData.String:
        values = geo.pointStringAttribValues(name_attrib) if is_point else geo.primStringAttribValues(name_attrib)
        return list(values)
    values = geo.pointIntAttribValues(name_attrib) if is_point else geo.primIntAttribValues(name_attrib)
    return [str(value) for value in values]


def _cooked_geometry(node: hou.Node) -> hou.Geometry:
    geo = node.geometry()
    if geo is None:
        raise hou.OperationFailed(f"Node has no geometry at frame {hou.frame()}: {node.path()}")
    return geo


def _norm(vector: Any) -> float:
    return math.sqrt(sum(component * component for component in vector))


###### diagnostics.track_named_elements

def _member_bounds(
    geo: hou.Geometry, element_class: str, members: list[int], positions: tuple[float, ...]
) -> dict[str, list[float]]:
    """Axis-aligned bounds of the named points, or of the named primitives."""
    if element_class == "point":
        lows = highs = [positions[3 * i:3 * i + 3] for i in members]
    else:
        boxes = [geo.prim(i).boundingBox() for i in members]
        lows = [box.minvec() for box in boxes]
        highs = [box.maxvec() for box in boxes]
    low = [min(corner[axis] for corner in lows) for axis in range(3)]
    high = [max(corner[axis] for corner in highs) for axis in range(3)]
    return {
        "min": _rounded(low),
        "max": _rounded(high),
        "center": _rounded((a + b) * 0.5 for a, b in zip(low, high, strict=True)),
    }


def track_named_elements(
    node_path: str,
    frames: list[float],
    names: list[str] | None = None,
    name_attrib: str = "name",
    attribs: list[str] | None = None,
    element_class: str = "point",
    include_bounds: bool = False,
    max_names: int = 50,
) -> dict[str, Any]:
    """Sample attributes (and optional bounds) of named elements at several frames."""
    if element_class not in ("point", "prim"):
        raise hou.OperationFailed("element_class must be 'point' or 'prim'")
    if not frames:
        raise hou.OperationFailed("frames must not be empty")
    node = _node_or_raise(node_path)
    attribs = attribs if attribs is not None else ["P"]

    original_frame = hou.frame()
    selected: list[str] = []
    names_total = 0
    samples: dict[str, list[dict[str, Any]]] = {}
    try:
        for frame in frames:
            hou.setFrame(frame)
            geo = _cooked_geometry(node)
            index: dict[str, list[int]] = {}
            for element, name in enumerate(_element_names(geo, element_class, name_attrib)):
                index.setdefault(name, []).append(element)
            if not samples:
                names_total = len(index)
                selected = list(names) if names else sorted(index)[:max_names]
                samples = {name: [] for name in selected}
            values = {
                attrib: _read_flat_values_by_class(geo, attrib, element_class) for attrib in attribs
            }
            positions = geo.pointFloatAttribValues("P") if include_bounds and element_class == "point" else ()
            for name in selected:
                members = index.get(name)
                if not members:
                    samples[name].append({"frame": frame, "missing": True})
                    continue
                row: dict[str, Any] = {"frame": frame, "count": len(members)}
                for attrib, (flat, size) in values.items():
                    chunk = flat[members[0] * size:(members[0] + 1) * size]
                    row[attrib] = round(chunk[0], 6) if size == 1 else _rounded(chunk)
                if include_bounds:
                    row["bounds"] = _member_bounds(geo, element_class, members, positions)
                samples[name].append(row)
    finally:
        hou.setFrame(original_frame)

    return {
        "node_path": node_path,
        "element_class": element_class,
        "name_attrib": name_attrib,
        "frames": list(frames),
        "names_total": names_total,
        "truncated": names is None and names_total > max_names,
        "samples": samples,
    }


register_handler("diagnostics.track_named_elements", track_named_elements)


###### diagnostics.rbd_motion_stats

def _reset_simulation(node_path: str) -> None:
    node = _node_or_raise(node_path)
    for parm_name in _RESET_BUTTONS:
        parm = node.parm(parm_name)
        if parm is not None:
            parm.pressButton()
            return
    raise hou.OperationFailed(f"No reset button ({', '.join(_RESET_BUTTONS)}) on {node_path}")


def _round3(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


@dataclass
class _MotionTally:
    """Per-piece state accumulated while a rigid-body result is stepped frame by frame."""

    up: int
    fps: float
    moving_speed: float
    rebound_speed: float
    origin: list[float] | None
    velocity_source: str | None = None
    previous: dict[str, tuple[float, ...]] = field(default_factory=dict)
    peak_up: dict[str, float] = field(default_factory=dict)
    peak_height: dict[str, float] = field(default_factory=dict)
    lowest: dict[str, Any] | None = None
    moving_by_frame: list[tuple[float, int]] = field(default_factory=list)

    def radial(self, position: tuple[float, ...]) -> float:
        return _norm([position[axis] - self.origin[axis] for axis in range(3) if axis != self.up])

    def _velocity(
        self, name: str, index: int, position: tuple[float, ...], velocities: tuple[float, ...] | None
    ) -> tuple[float, ...] | None:
        if velocities is not None:
            return velocities[3 * index:3 * index + 3]
        if name in self.previous:
            return tuple((a - b) * self.fps for a, b in zip(position, self.previous[name], strict=True))
        return None

    def add_frame(self, frame: float, geo: hou.Geometry, name_attrib: str, count_rebound: bool) -> dict[str, Any]:
        """Fold one frame into the running state and return that frame's summary row."""
        names = _element_names(geo, "point", name_attrib)
        positions = geo.pointFloatAttribValues("P")
        velocities = geo.pointFloatAttribValues("v") if geo.findPointAttrib("v") else None
        spins = geo.pointFloatAttribValues("w") if geo.findPointAttrib("w") else None
        self.velocity_source = "v attribute" if velocities is not None else "finite difference of P"
        if self.origin is None:
            self.origin = [sum(positions[axis::3]) / max(len(names), 1) for axis in range(3)]

        current = {}
        speeds, radials, heights, spin_rates = [], [], [], []
        for i, name in enumerate(names):
            position = positions[3 * i:3 * i + 3]
            current[name] = position
            velocity = self._velocity(name, i, position, velocities)
            if velocity is not None:
                speeds.append(_norm(velocity))
                if count_rebound and velocity[self.up] > self.rebound_speed:
                    self.peak_up[name] = max(self.peak_up.get(name, 0.0), velocity[self.up])
            radials.append(self.radial(position))
            heights.append(position[self.up])
            self.peak_height[name] = max(self.peak_height.get(name, -math.inf), position[self.up])
            if self.lowest is None or position[self.up] < self.lowest["height"]:
                self.lowest = {"height": round(position[self.up], 4), "name": name, "frame": frame}
            if spins is not None:
                spin_rates.append(_norm(spins[3 * i:3 * i + 3]))
        self.previous = current

        moving = sum(1 for speed in speeds if speed > self.moving_speed)
        if speeds:
            self.moving_by_frame.append((frame, moving))
        speeds.sort()
        radials.sort()
        return {
            "frame": frame,
            "pieces": len(names),
            "speed_p50": _round3(_percentile(speeds, 0.5)),
            "speed_max": _round3(_percentile(speeds, 1.0)),
            "moving": moving,
            "radial_p50": _round3(_percentile(radials, 0.5)),
            "radial_p90": _round3(_percentile(radials, 0.9)),
            "radial_max": _round3(_percentile(radials, 1.0)),
            "height_min": _round3(min(heights)) if heights else None,
            "height_max": _round3(max(heights)) if heights else None,
            "spin_max": _round3(max(spin_rates)) if spin_rates else None,
        }

    def settle_frame(self) -> float | None:
        """First frame from which nothing moves through the end, or None if still moving."""
        settle = None
        for frame, moving in reversed(self.moving_by_frame):
            if moving:
                return settle
            settle = frame
        return settle

    def summary(self, top_n: int) -> dict[str, Any]:
        final_radial = {name: self.radial(position) for name, position in self.previous.items()}
        farthest = sorted(final_radial, key=final_radial.get, reverse=True)[:top_n]
        rebounds = sorted(self.peak_up.values())
        return {
            "center": _rounded(self.origin, 4),
            "velocity_source": self.velocity_source,
            "settle_frame": self.settle_frame(),
            "lowest": self.lowest,
            "rebound": {
                "pieces": len(rebounds),
                "up_speed_p50": _round3(_percentile(rebounds, 0.5)),
                "up_speed_p90": _round3(_percentile(rebounds, 0.9)),
                "up_speed_max": _round3(_percentile(rebounds, 1.0)),
            },
            "farthest": [
                {
                    "name": name,
                    "radial": _round3(final_radial[name]),
                    "height": _round3(self.previous[name][self.up]),
                    "peak_height": _round3(self.peak_height[name]),
                }
                for name in farthest
            ],
        }


def rbd_motion_stats(
    node_path: str,
    start_frame: float,
    end_frame: float,
    sample_frames: list[float] | None = None,
    name_attrib: str = "name",
    center: list[float] | None = None,
    up_axis: str = "y",
    moving_speed: float = 0.3,
    rebound_speed: float = 0.5,
    reset_node_path: str | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """Step a rigid-body output frame by frame and summarise spread, speed, spin, and settling."""
    up = _UP_AXES.get(up_axis.lower())
    if up is None:
        raise hou.OperationFailed("up_axis must be 'x', 'y', or 'z'")
    node = _node_or_raise(node_path)
    frames = _all_frames(float(start_frame), float(end_frame), 1.0)
    if len(frames) < 2:
        raise hou.OperationFailed("end_frame must be after start_frame")
    report_frames = set(sample_frames) if sample_frames else set(_sample_frames(frames, _DEFAULT_SAMPLE_COUNT))
    if reset_node_path:
        _reset_simulation(reset_node_path)

    tally = _MotionTally(
        up=up,
        fps=hou.fps(),
        moving_speed=moving_speed,
        rebound_speed=rebound_speed,
        origin=list(center) if center is not None else None,
    )
    rows = []
    started = time.time()
    original_frame = hou.frame()
    try:
        for frame in frames:
            hou.setFrame(frame)
            row = tally.add_frame(frame, _cooked_geometry(node), name_attrib, count_rebound=frame > frames[0])
            if frame in report_frames:
                rows.append(row)
    finally:
        hou.setFrame(original_frame)

    result = {
        "node_path": node_path,
        "frame_range": [frames[0], frames[-1]],
        "fps": tally.fps,
        "up_axis": up_axis.lower(),
        "samples": rows,
    }
    result.update(tally.summary(top_n))
    result["seconds"] = round(time.time() - started, 2)
    return result


register_handler("diagnostics.rbd_motion_stats", rbd_motion_stats)
