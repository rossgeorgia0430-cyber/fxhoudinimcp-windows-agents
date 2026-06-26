# :material-wrench:{.scale-in-center} Tools

## Overview

fxhoudinimcp exposes **205 tools** across **24 categories**, covering every major Houdini context.

> **Recently extended (+21 tools).** New **Image** category — `inspect_image`,
> `sample_image`, `image_region_stats` (EXR/texture inspection via
> OpenImageIO) — plus render/verify additions: `render_rop` (button-aware HDA
> bake) & `list_rop_outputs` & async `start_render_job`/`get_render_job`;
> `attribute_stats`/`compare_frames`/`verify_animation` (per-frame geometry
> checks); `set_fps`/`scene_set_frame*`/`press_button`;
> `get_menu_items`/`get_hda_help`/`get_node_messages`.

> **Also new (+4 VAT tools).** New **VAT** category for the SideFX Labs
> VAT → UE workflow — `bake_attribute_to_spatial_atlas` &
> `reshape_to_spatial_atlas` (spatial flipbook normal/height atlases),
> `get_vat_layout` (texture-layout introspection), and `sample_vertex`
> (per-vertex frozen-vs-animated check).

Once connected, your AI assistant can:

``` text
"Create a procedural rock generator with mountain displacement"
"Set up a Pyro simulation with a sphere source"
"Build a USD scene with a camera, dome light, and ground plane"
"Create an HDA from the selected subnet"
"Debug why my scene has cooking errors"
```

## Categories

### Graph Intelligence (4 tools)

Senior-artist tooling: `build_network` builds whole validated networks
atomically (with dry-run plan checking), `verify_network` inspects every
node's errors and cooked geometry, `get_node_card` serves version-exact
node documentation from the running Houdini, and `find_expensive_nodes`
profiles cook costs.

### Documentation (3 tools)

`search_help` runs full-text search over the documentation Houdini
ships in `$HFS/houdini/help` (nodes, VEX, expressions, HOM, Solaris,
TOPs, character, reference) — always version-exact, no network needed.
`get_help_page` fetches a full page. On builds without local help, both
degrade into a clear error pointing at `get_node_card` and the online
docs. `get_hda_help` returns an HDA type's description plus any embedded
help and per-parameter docs (it falls back to `search_help` for nodes
whose manual lives in `$HFS/houdini/help` rather than embedded).

### Scene Management (13 tools)

Open, save, import/export, query scene information, and inspect the Houdini
connection status. Scene-time control: `set_fps`/`get_fps` drive the global
FPS, and `scene_set_frame_range`/`scene_set_frame`/`scene_get_frame` move and
read the playbar.

### Node Operations (19 tools)

Create, delete, copy, connect, layout nodes, and manage flags. `get_menu_items`
resolves a parameter menu's real labels (a card only exposes the stored token),
and `get_node_messages` returns a node's errors and warnings counted separately.

### Parameters (12 tools)

Get/set parameter values, expressions, keyframes, and spare parameters.
`press_button` fires a button/callback parameter (for example an HDA's
Reload or a ROP's Render).

### Geometry / SOPs (15 tools)

Read points, primitives, attributes, groups. Perform sampling and nearest-point
searches. Per-frame verification: `attribute_stats` summarises an attribute's
min/max/mean, `compare_frames` diffs a SOP's geometry between two frames (so a
seamless loop can be proven, e.g. `P(f1) ≡ P(f151)`), and `verify_animation`
confirms a deforming output actually changes across a range.

### LOPs/USD (18 tools)

Stage inspection, USD prims, layers, composition arcs, variants, and lighting setup.

### DOPs (8 tools)

Query simulation info, DOP objects, step/reset simulations, and check memory usage.

### PDG/TOPs (10 tools)

Cook tasks, inspect work items, manage schedulers and dependency graphs.

### COPs / Copernicus (7 tools)

Image nodes, layers, and VDB data access.

### HDAs (10 tools)

Create, install, and manage Houdini Digital Assets and their sections.

### Animation (9 tools)

Set keyframes, control the playbar, and manage frame ranges.

### Rendering (15 tools)

Viewport capture, render node management, render settings, and launch renders.
`render_rop` drives a ROP/Driver — including button-aware HDA bakes (e.g.
SideFX Labs VAT "Render All", which a plain `node.render()` silently leaves
empty); `list_rop_outputs` resolves a ROP's output paths and stats them; and
`start_render_job`/`get_render_job`/`list_render_jobs`/`cancel_render_job` run
a long render in a detached `hython` process so it never blocks the session.

### Image (3 tools)

Inspect EXR/texture files on disk without leaving Houdini. `inspect_image`
reports dimensions, channels, and per-channel statistics; `sample_image`
reads exact pixel values at a coordinate; `image_region_stats` aggregates a
sub-region. Built on OpenImageIO (ships with Houdini) — ideal for verifying
baked VAT position/color textures.

### VAT (4 tools)

SideFX Labs VAT → UE helpers. `bake_attribute_to_spatial_atlas` cooks a point
attribute over a frame range into a spatial flipbook atlas EXR (the recommended
way to make a UE normal/height atlas, bypassing the vertex-index VAT layout);
`reshape_to_spatial_atlas` reshapes an already-baked vertex-index VAT EXR into
that spatial atlas; `get_vat_layout` introspects a VAT ROP's texture layout and
vertex-index↔texel mapping; `sample_vertex` reads one vertex's VAT texel across
frames to report whether a channel is frozen or animated.

### VEX (5 tools)

Create/edit wrangle nodes and validate VEX code.

### Code Execution (4 tools)

Execute Python, HScript, expressions, and manage environment variables.

### Viewport/UI (13 tools)

Pane management, viewport screenshots, and error detection.

### Scene Context (8 tools)

Network overview, cook chains, selection state, scene summaries, and error analysis.

### Workflows (8 tools)

One-call Pyro, RBD, FLIP, and Vellum simulation setup. SOP chains and render configuration.

### Materials (5 tools)

List, inspect, create, and assign materials and shader networks.

### CHOPs (4 tools)

Channel data access, CHOP node management, and channel-to-parameter export.

### Cache (4 tools)

List, inspect, clear, and write file caches.

### Takes (4 tools)

List, create, and switch takes with parameter overrides.
