# FXHoudini MCP - Windows Agents guidance

This repository is a Windows-focused FXHoudini MCP fork for Codex and Claude
Code. Use the fork-local `.venv`, the newest installed Houdini unless a task
passes `-HoudiniRoot`, and the scripts in `scripts/windows`; do not silently
run a system Python or the source repository beside this fork.

## Completion standard

For an MCP change, run ruff, the normal pytest suite, the full live Houdini
integration suite, HTTP bridge E2E, stdio MCP E2E, performance gate, and the
GUI visual regression gate when the change affects tools, transport, images,
or Houdini handlers. Report the exact commands and outcomes.

## Houdini work style

Use scene inspection, node discovery/card lookup, graph dry-run, graph build,
verification, and visual capture as the normal sequence. Preserve all 205 MCP
tools in this fork (the catalog grew from 180 with the render/image/geometry
verification additions, then +4 with the `vat` module; the `image` and `vat`
modules join the `core` profile and the render helpers surface in `core` via
`_PROFILE_EXTRA_TOOLS`). Prefer typed, native tool contracts over opaque JSON.

The `vat` module (`tools/vat.py` + `handlers/vat_handlers.py`) turns the
hand-written scripts of the SideFX Labs VAT -> UE smooth-normal workflow into
native tools: `bake_attribute_to_spatial_atlas` (cook a point attribute over a
frame range into a spatial flipbook atlas EXR — the recommended way to produce
a UE normal/height atlas, bypassing the vertex-index VAT layout),
`reshape_to_spatial_atlas` (reshape an already-baked vertex-index VAT EXR into a
spatial atlas, pure numpy/OIIO), `get_vat_layout` (introspect a VAT ROP's
texture layout + vertex-index<->texel mapping), and `sample_vertex` (read one
vertex's VAT texel across frames to confirm frozen-vs-animated). The shared
helpers at the top of `vat_handlers.py` (OIIO read/write, gutter fill, Houdini
Y-up -> UE Z-up normal encode, frame-list / tile resolution) are the
correctness-critical pieces — change them deliberately and re-run the offline
reshape test.

## Multi-agent development workflow

When a change is large enough to warrant it, split the work so the strongest
model reviews and the cheaper model writes: **a high-capability model (e.g.
Opus) acts as the lead — it owns the design, the correctness-critical shared
code, integration/wiring, review, and live testing/verification against the
running Houdini session; the pure code-writing of independent, well-specified
units is delegated to any number of cheaper coder subagents (e.g. Sonnet),
which return code without touching shared registration files.** The lead then
assembles, reviews each unit (axis/index conventions, numpy/OIIO API use,
defensive validation), fixes the hard problems, and verifies. This keeps
consistency bugs (the costly kind) under the reviewer's control while
parallelizing the mechanical work. The `vat` module was built this way.

For HIP spare-parameter edits, do not trust a bridge save response by itself.
When a task changes `parmTemplateGroup()` or spare parameters, reload the saved
HIP in a separate Hython process and inspect the target node. Use
`scripts/windows/verify-hip-node-params.ps1` for this check; it was added after
an agent session where `save_scene` reported success but the disk HIP did not
contain the new spare parameters.

## Windows runtime

Run `scripts/windows/install-houdini-package.ps1` once, then launch the GUI
through `scripts/windows/start-houdini-fork.ps1 -Visible`. It isolates this
clone from other FXHoudini packages and configures port 18100. Start Codex with
this repository's `.codex/config.toml`, or approve the project `.mcp.json` in
Claude Code. GUI tests require an interactive Windows desktop session.
