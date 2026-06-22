[CmdletBinding()]
param(
    [string]$Python,
    [switch]$Dev,
    [switch]$Recreate,
    [switch]$SkipHoudiniPackage,
    [string]$HoudiniRoot,
    [string]$HoudiniVersion,
    [int]$Port = 18100
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$venv = Join-Path $repo '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'

# Windows PowerShell 5 and Python 3.10 default to legacy code pages on GitHub's
# Windows runners. Force UTF-8 so pip can install from Unicode checkout paths.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:NO_COLOR = '1'

function Get-PythonInvocation {
    param([string]$RequestedPython)

    $candidates = New-Object System.Collections.Generic.List[object]
    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
        $expanded = [Environment]::ExpandEnvironmentVariables($RequestedPython.Trim('"'))
        $candidates.Add([pscustomobject]@{ Command = $expanded; Prefix = @() })
    } else {
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($null -ne $py) {
            foreach ($selector in @('-3.12', '-3.11', '-3.10')) {
                $candidates.Add([pscustomobject]@{ Command = $py.Source; Prefix = @($selector) })
            }
        }
        foreach ($name in @('python.exe', 'python3.exe')) {
            $command = Get-Command $name -ErrorAction SilentlyContinue
            if ($null -ne $command) {
                $candidates.Add([pscustomobject]@{ Command = $command.Source; Prefix = @() })
            }
        }
    }

    $versionProbe = "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    foreach ($candidate in $candidates) {
        $prefix = $candidate.Prefix
        $versionText = & $candidate.Command @prefix -c $versionProbe 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($versionText)) { continue }
        try {
            $version = [version]($versionText | Select-Object -Last 1)
        } catch {
            continue
        }
        if ($version -ge [version]'3.10.0') {
            return [pscustomobject]@{
                Command = $candidate.Command
                Prefix = $candidate.Prefix
                Version = $version
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($RequestedPython)) {
        throw 'Python 3.10 or newer was not found. Install Python, or pass -Python with an executable path.'
    }
    throw "The requested Python '$RequestedPython' is unavailable or older than 3.10."
}

if ($Recreate -and (Test-Path -LiteralPath $venv)) {
    $repoFull = [IO.Path]::GetFullPath($repo).TrimEnd('\')
    $venvFull = [IO.Path]::GetFullPath($venv).TrimEnd('\')
    if (-not $venvFull.StartsWith("$repoFull\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a virtual environment outside the repository: '$venvFull'."
    }
    Remove-Item -LiteralPath $venvFull -Recurse -Force
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $basePython = Get-PythonInvocation -RequestedPython $Python
    $prefix = $basePython.Prefix
    Write-Output "Creating .venv with Python $($basePython.Version): $($basePython.Command) $($prefix -join ' ')"
    & $basePython.Command @prefix -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Python failed to create the virtual environment at '$venv'."
    }
}

$venvVersion = & $venvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0) {
    throw "The existing virtual environment is not usable. Run this script again with -Recreate."
}
if ([version]$venvVersion -lt [version]'3.10.0') {
    throw "The existing virtual environment uses Python $venvVersion. Run this script again with -Recreate and Python 3.10+."
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }

$editableTarget = if ($Dev) { '.[dev]' } else { '.' }
Push-Location $repo
try {
    & $venvPython -m pip install -e $editableTarget
    if ($LASTEXITCODE -ne 0) { throw 'FXHoudiniMCP installation failed.' }
} finally {
    Pop-Location
}

if (-not $SkipHoudiniPackage) {
    $installArguments = @{ Port = $Port }
    if (-not [string]::IsNullOrWhiteSpace($HoudiniRoot)) {
        $installArguments.HoudiniRoot = $HoudiniRoot
    }
    if (-not [string]::IsNullOrWhiteSpace($HoudiniVersion)) {
        $installArguments.HoudiniVersion = $HoudiniVersion
    }
    & (Join-Path $PSScriptRoot 'install-houdini-package.ps1') @installArguments
}

Write-Output ''
Write-Output 'Bootstrap complete.'
Write-Output "Python: $venvPython"
Write-Output "Start Houdini: '$PSScriptRoot\start-houdini-fork.ps1' -Visible -Port $Port"
Write-Output 'Then reopen this folder in Codex, or approve the project server in Claude Code.'
