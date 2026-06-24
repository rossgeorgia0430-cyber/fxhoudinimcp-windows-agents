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
verification, and visual capture as the normal sequence. Preserve all 201 MCP
tools in this fork (the catalog grew from 180 with the render/image/geometry
verification additions; the new `image` module joins the `core` profile and
the render helpers surface in `core` via `_PROFILE_EXTRA_TOOLS`). Prefer typed,
native tool contracts over opaque JSON.

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
