"""Pydantic parameter models for the composite graph tools.

Strongly typing the nested specs of ``build_network`` and
``connect_nodes_batch`` gives the agent a real JSON Schema — field names,
types, defaults, and no stray keys — instead of an opaque ``dict[str, Any]``
it has to assemble from the docstring prose. ``parms`` is deliberately left
open (a node's parameter namespace is dynamic and can't be enumerated), but
the surrounding structure is typed and ``extra="forbid"`` catches typos in the
spec keys before anything is sent to Houdini.

The handlers still receive plain dicts: the wrappers ``model_dump`` these
models before sending them over the bridge.
"""

from __future__ import annotations

# Third-party
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Internal
from fxhoudinimcp._types import Value


NonNegativeInt = Annotated[int, Field(ge=0)]
ColorComponent = Annotated[float, Field(ge=0.0, le=1.0)]
Color = Annotated[list[ColorComponent], Field(min_length=3, max_length=3)]


class StrictSpec(BaseModel):
    """Base model for nested tool contracts.

    Houdini parameter names remain dynamic, but every structural field that
    controls graph creation, connections, or batch mutation is explicit.
    This is deliberately more useful to an agent than a broad ``dict`` whose
    valid shape lives only in prose.
    """

    model_config = ConfigDict(extra="forbid")


class InputSpec(StrictSpec):
    """One wired input on a node in ``build_network``."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description=(
            "Input source: an earlier spec's name, an existing child of the "
            "parent, or an absolute node path."
        )
    )
    index: NonNegativeInt | None = Field(
        default=None,
        description="Destination input index. Defaults to this entry's position.",
    )
    source_output: NonNegativeInt = Field(
        default=0, description="Which output of the source node to wire from."
    )


class NodeFlags(StrictSpec):
    """Optional display flags for a node created by ``build_network``."""

    display: bool | None = None
    render: bool | None = None
    bypass: bool | None = None
    template: bool | None = None


class NodeSpec(StrictSpec):
    """One node to create in ``build_network``."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(
        description="Node type name; unversioned is fine (e.g. 'box', 'scatter')."
    )
    name: str | None = Field(
        default=None,
        description="Node name, referenceable as an input source by later specs.",
    )
    parms: dict[str, Value] | None = Field(
        default=None,
        description=(
            "Parameter values. A list sets a whole parm tuple, e.g. "
            "{'t': [0, 1, 0], 'scale': 2}."
        ),
    )
    inputs: list[InputSpec | str] | None = Field(
        default=None,
        description=(
            "Inputs in order. Each entry is either a source string (wired "
            "positionally) or an InputSpec for explicit index/output control."
        ),
    )
    flags: NodeFlags | None = Field(
        default=None,
        description="Node flags: display / render / bypass / template.",
    )
    color: Color | None = Field(
        default=None, description="Network-editor color as [r, g, b], 0-1 each."
    )
    comment: str | None = Field(
        default=None, description="Network sticky comment shown on the node."
    )


class ConnectionSpec(StrictSpec):
    """One connection for ``connect_nodes_batch``."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(description="Upstream (source) node path.")
    dest_path: str = Field(description="Downstream (destination) node path.")
    output_index: NonNegativeInt = Field(default=0, description="Source output index.")
    input_index: NonNegativeInt = Field(default=0, description="Destination input index.")


class KeyframeSpec(StrictSpec):
    """One keyframe used by ``set_keyframes``."""

    frame: float
    value: float
    slope: float | None = None
    accel: float | None = None


class SpareParameterSpec(StrictSpec):
    """A user-facing spare parameter definition."""

    parm_name: str
    parm_type: Literal["float", "int", "string", "toggle", "menu"]
    label: str
    default_value: Value | None = None
    min_val: float | None = None
    max_val: float | None = None


class SopStepSpec(StrictSpec):
    """One sequential SOP creation step used by ``build_sop_chain``."""

    type: str
    name: str | None = None
    params: dict[str, Value] | None = None
