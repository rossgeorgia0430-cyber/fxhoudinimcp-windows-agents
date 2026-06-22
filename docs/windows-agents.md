# Windows Agents

This fork ships project-scoped Codex and Claude Code MCP profiles plus
portable PowerShell launchers. The checkout may live on any drive or under a
path containing spaces or Unicode characters. Houdini 20.5+ and Python 3.10+
are supported; the scripts select the newest installed Houdini and a suitable
Python automatically.

## One-time setup

Open PowerShell in the cloned repository and run:

```powershell
.\scripts\windows\bootstrap.ps1
```

The bootstrap script:

1. finds Python 3.12, 3.11, or 3.10 (or accepts `-Python`);
2. creates the repository-local `.venv`;
3. installs the MCP server in editable mode; and
4. detects Houdini and installs a uniquely named user package.

Development dependencies are optional:

```powershell
.\scripts\windows\bootstrap.ps1 -Dev
```

Use explicit overrides only when auto-detection cannot choose the desired
installation:

```powershell
.\scripts\windows\bootstrap.ps1 `
  -Python "<python.exe>" `
  -HoudiniRoot "<houdini-root>" `
  -Port 18100
```

If a copied checkout contains a virtual environment from another computer,
rebuild it rather than copying that environment:

```powershell
.\scripts\windows\bootstrap.ps1 -Recreate
```

## Start Houdini and an Agent Client

```powershell
.\scripts\windows\start-houdini-fork.ps1 -Port 18100 -Visible
```

The launcher automatically selects the newest installed Houdini. It uses an
isolated preferences directory below the current Windows user's Local AppData,
loads this checkout's `houdini/` directory, and starts the bridge on port
18100. Select a particular installation with `-HoudiniRoot`.

Open this repository as a trusted Codex project. Its `.codex/config.toml` uses
`[mcp_servers.fxhoudinimcp]` with only project-relative paths, starts the
repository-local MCP server, and forwards the same port. Inspect the connection
with `/mcp`.

The `[projects.<path>]` trust entry alone is not an MCP registration. Codex
needs the `[mcp_servers.fxhoudinimcp]` section to expose the Houdini tools.

Claude Code reads the shared `.mcp.json` project server with the same
`fxhoudinimcp` name, launcher, port, and tool profile. Claude Code marks new
shared project MCP servers as pending approval; run `claude` once in the
checkout and approve the project server before expecting `claude mcp list` to
health-check it.

## Houdini package behavior

The normal user-package installer is available separately:

```powershell
.\scripts\windows\install-houdini-package.ps1 -Port 18100
```

It resolves the Windows Documents known folder through the operating system,
so redirected and OneDrive-backed Documents folders are supported. The
package is written as:

```text
<Documents>\houdini<major.minor>\packages\fxhoudinimcp-windows-agents.json
```

The installer does not overwrite `fxhoudinimcp.json` and does not modify the
global `package.pref`. If a different file already uses the same unique package
name, it is saved as `.json.backup`. If package auto-loading is globally
disabled, the installer prints a warning and leaves that preference untouched.

To remove this fork's package and restore a same-name backup:

```powershell
.\scripts\windows\uninstall-houdini-package.ps1
```

For a redirected Documents root, use `-DocumentsPath`; the script appends the
Houdini version directory:

```powershell
.\scripts\windows\install-houdini-package.ps1 `
  -HoudiniVersion 21.0 `
  -DocumentsPath "<redirected-documents>"
```

For a fully custom preferences location, pass the exact
`houdini<major.minor>` directory to both install and uninstall:

```powershell
.\scripts\windows\install-houdini-package.ps1 `
  -HoudiniVersion 21.0 `
  -HoudiniPreferencesRoot "<houdini-preferences-root>"
```

## Full validation

```powershell
.\scripts\windows\run-full-validation.ps1
```

The validation script runs lint, unit/schema tests, live Houdini integration,
HTTP and stdio MCP end-to-end checks, performance checks, and a GUI visual
test. GUI validation requires an interactive desktop session and an available
Houdini license.

## Verify HIP spare parameters

When an agent edits spare parameters or a node `parmTemplateGroup()`, verify the
saved HIP from a fresh Hython process instead of relying only on a bridge
`save_scene` success response:

```powershell
.\scripts\windows\verify-hip-node-params.ps1 `
  -HipFile "<hip-file>" `
  -NodePath "/obj/geo1/example_node" `
  -ExpectedParm example_repeat,example_scale,example_size `
  -ExpectedRootParm example_repeat,example_scale,example_size
```

The script prints JSON containing the node's spare parameters and root-level
parameter-template entries, then exits non-zero if any expected parameter is
missing. This helper is intentionally Hython-based so it verifies the disk HIP
state, not the current bridge session state.

## Troubleshooting

- If bootstrap cannot find Python, pass the full executable path with
  `-Python`.
- If multiple Houdini builds are installed, pass `-HoudiniRoot` to select one.
- The Houdini bridge and MCP client ports must match. This profile defaults both
  to `18100`.
- If Houdini did not load `uiready.py`, start the bridge from Houdini's Python
  shell with a clone-local path:

  ```python
  import os
  import sys

  repo = r"<clone-root>"
  sys.path.insert(0, os.path.join(repo, "houdini", "scripts", "python"))

  import fxhoudinimcp_server.startup as mcp

  mcp.start(int(os.environ.get("FXHOUDINIMCP_PORT", "18100")))
  print("MCP port", mcp.get_port())
  ```
- Do not copy `.venv` between machines. Run `bootstrap.ps1 -Recreate`.
- If another FXHoudiniMCP package is active, disable it in Houdini's package
  manager or assign non-conflicting ports.
