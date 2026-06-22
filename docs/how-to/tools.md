# :material-wrench:{.scale-in-center} Tools

## Overview

fxhoudinimcp exposes **180 tools** across **22 categories**, covering every major Houdini context.

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

### Documentation (2 tools)

`search_help` runs full-text search over the documentation Houdini
ships in `$HFS/houdini/help` (nodes, VEX, expressions, HOM, Solaris,
TOPs, character, reference) — always version-exact, no network needed.
`get_help_page` fetches a full page. On builds without local help, both
degrade into a clear error pointing at `get_node_card` and the online
docs.

### Scene Management (7 tools)

Open, save, import/export, query scene information, and inspect the Houdini
connection status.

### Node Operations (17 tools)

Create, delete, copy, connect, layout nodes, and manage flags.

### Parameters (11 tools)

Get/set parameter values, expressions, keyframes, and spare parameters.

### Geometry / SOPs (12 tools)

Read points, primitives, attributes, groups. Perform sampling and nearest-point searches.

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

### Rendering (9 tools)

Viewport capture, render node management, render settings, and launch renders.

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

### Materials (6 tools)

List, inspect, create, and assign materials and shader networks.

### CHOPs (4 tools)

Channel data access, CHOP node management, and channel-to-parameter export.

### Cache (4 tools)

List, inspect, clear, and write file caches.

### Takes (4 tools)

List, create, and switch takes with parameter overrides.
