[CmdletBinding()]
param(
    [int]$Port = 18100,
    [switch]$Visible,
    [string]$HoudiniRoot,
    [string]$PreferencesRoot
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

function Find-HoudiniRoot {
    param([string]$RequestedRoot)

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        $expandedRequestedRoot = [Environment]::ExpandEnvironmentVariables($RequestedRoot.Trim('"'))
        $requestedExecutable = [IO.Path]::Combine($expandedRequestedRoot, 'bin', 'houdini.exe')
        if (Test-Path -LiteralPath $requestedExecutable) {
            return (Resolve-Path -LiteralPath $expandedRequestedRoot).Path
        }
        throw "No houdini.exe exists below the requested root '$RequestedRoot'."
    }
    if (-not [string]::IsNullOrWhiteSpace($env:HFS)) {
        $candidates.Add($env:HFS)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:HOUDINI_ROOT)) {
        $candidates.Add($env:HOUDINI_ROOT)
    }

    $command = Get-Command houdini.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        $candidates.Add((Split-Path -Parent (Split-Path -Parent $command.Source)))
    }

    $sideFxRegistry = 'HKLM:\SOFTWARE\Side Effects Software'
    if (Test-Path -LiteralPath $sideFxRegistry) {
        Get-ChildItem -LiteralPath $sideFxRegistry -ErrorAction SilentlyContinue |
            Where-Object { $_.PSChildName -match '^Houdini\s+\d+\.\d+(?:\.\d+)?$' } |
            Sort-Object {
                $match = [regex]::Match($_.PSChildName, '(\d+)\.(\d+)(?:\.(\d+))?')
                $build = if ($match.Groups[3].Success) { $match.Groups[3].Value } else { '0' }
                [version]("$($match.Groups[1].Value).$($match.Groups[2].Value).$build")
            } -Descending |
            ForEach-Object {
                $installPath = (Get-ItemProperty -LiteralPath $_.PSPath -Name InstallPath -ErrorAction SilentlyContinue).InstallPath
                if (-not [string]::IsNullOrWhiteSpace($installPath)) {
                    $candidates.Add($installPath)
                }
            }
    }

    $installBase = Join-Path $env:ProgramFiles 'Side Effects Software'
    if (Test-Path -LiteralPath $installBase) {
        Get-ChildItem -LiteralPath $installBase -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^Houdini\s+\d+\.\d+(?:\.\d+)?$' } |
            Sort-Object {
                $match = [regex]::Match($_.Name, '(\d+)\.(\d+)(?:\.(\d+))?')
                $build = if ($match.Groups[3].Success) { $match.Groups[3].Value } else { '0' }
                if ($match.Success) {
                    [version]("$($match.Groups[1].Value).$($match.Groups[2].Value).$build")
                } else {
                    [version]'0.0.0'
                }
            } -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $resolvedCandidate = [Environment]::ExpandEnvironmentVariables($candidate.Trim('"'))
        $candidateExecutable = [IO.Path]::Combine($resolvedCandidate, 'bin', 'houdini.exe')
        if (Test-Path -LiteralPath $candidateExecutable) {
            return (Resolve-Path -LiteralPath $resolvedCandidate).Path
        }
    }

    throw 'Unable to locate a Houdini installation. Install Houdini or pass -HoudiniRoot.'
}

$HoudiniRoot = Find-HoudiniRoot -RequestedRoot $HoudiniRoot
$houdini = Join-Path $HoudiniRoot 'bin\houdini.exe'

# Keep this fork independent of an existing checkout's packages and startup
# files. Houdini only honors a user-preference override when it contains the
# literal __HVER__ token.
$prefsRoot = if ([string]::IsNullOrWhiteSpace($PreferencesRoot)) {
    Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'FXHoudiniMCP\codex-windows'
} else {
    [Environment]::ExpandEnvironmentVariables($PreferencesRoot)
}
$env:HOUDINI_USER_PREF_DIR = "$prefsRoot\__HVER__"
$env:HOUDINI_PATH = "$(Join-Path $repo 'houdini');&"
$env:FXHOUDINIMCP_PORT = "$Port"
$env:FXHOUDINIMCP_AUTOSTART = '1'

$startArgs = @{
    FilePath = $houdini
    PassThru = $true
}
if (-not $Visible) {
    $startArgs.WindowStyle = 'Hidden'
}
$process = Start-Process @startArgs
Write-Output "Started fork-local Houdini (pid $($process.Id), port $Port)."
Write-Output "Houdini root: $HoudiniRoot"
Write-Output "HOUDINI_USER_PREF_DIR=$env:HOUDINI_USER_PREF_DIR"
