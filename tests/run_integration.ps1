# Runs the integration test suite inside hython (Houdini's Python).
#
# Usage:
#   tests/run_integration.ps1                 # whole integration suite
#   tests/run_integration.ps1 -k materials    # pytest args pass through
#
# Requires a Houdini installation (consumes one license seat) and pytest
# installed for a CPython matching hython's major.minor version.
# Override auto-detection with the HYTHON environment variable.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

if ($env:HYTHON -and (Test-Path $env:HYTHON)) {
    $hython = $env:HYTHON
} else {
    $installs = Get-ChildItem "C:\Program Files\Side Effects Software" -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "^Houdini \d+\.\d+" } |
        Sort-Object { [version]($_.Name -replace "Houdini ", "") } -Descending
    $hython = $installs |
        ForEach-Object { Join-Path $_.FullName "bin\hython.exe" } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
    if (-not $hython) {
        throw "No hython.exe found under 'C:\Program Files\Side Effects Software'. Set HYTHON to its full path."
    }
}

# Reuse the system Python's site-packages for pytest and plugins.
$sitePackages = & python -c "import pytest, os; print(os.path.dirname(os.path.dirname(pytest.__file__)))"
if ($LASTEXITCODE -ne 0) {
    throw "Could not locate pytest via 'python'. Install it: python -m pip install pytest pytest-asyncio"
}

$env:PYTHONPATH = "$repoRoot\python;$sitePackages"
Write-Host "Using hython: $hython"

& $hython -m pytest "$repoRoot\tests\integration" -q -s --durations=15 @args
exit $LASTEXITCODE
