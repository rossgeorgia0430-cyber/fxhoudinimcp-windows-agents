[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    $bootstrap = Join-Path $PSScriptRoot 'bootstrap.ps1'
    throw "The project virtual environment was not found at '$python'. Run '$bootstrap' once, then restart Codex."
}

$pythonPath = Join-Path $repo 'python'
if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $env:PYTHONPATH = $pythonPath
} else {
    $separator = [IO.Path]::PathSeparator
    $env:PYTHONPATH = "$pythonPath$separator$env:PYTHONPATH"
}

if ([string]::IsNullOrWhiteSpace($env:HOUDINI_HOST)) { $env:HOUDINI_HOST = '127.0.0.1' }
if ([string]::IsNullOrWhiteSpace($env:HOUDINI_PORT)) { $env:HOUDINI_PORT = '18100' }

& $python -m fxhoudinimcp
exit $LASTEXITCODE
