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
    Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'FXHoudiniMCP\windows-agents'
} else {
    [Environment]::ExpandEnvironmentVariables($PreferencesRoot)
}
$env:HOUDINI_USER_PREF_DIR = "$prefsRoot\__HVER__"
$env:HOUDINI_PATH = "$(Join-Path $repo 'houdini');&"
$env:FXHOUDINIMCP_PORT = "$Port"
$env:HOUDINI_PORT = "$Port"
$env:FXHOUDINIMCP_AUTOSTART = '1'

# Start-Process (UseShellExecute=true) does not reliably pass this session's
# environment to houdini.exe. ProcessStartInfo with UseShellExecute=false
# *does* pass env, but H22's GUI stub then exits immediately. Launch via
# cmd.exe so `set` mutates the child environment and `start` uses the
# Windows shell for the GUI process.
$houdiniDir = Split-Path -Parent $houdini
$cmdParts = @(
    "set `"HOUDINI_USER_PREF_DIR=$($env:HOUDINI_USER_PREF_DIR)`"",
    "set `"HOUDINI_PATH=$($env:HOUDINI_PATH)`"",
    "set `"FXHOUDINIMCP_PORT=$($env:FXHOUDINIMCP_PORT)`"",
    "set `"HOUDINI_PORT=$($env:HOUDINI_PORT)`"",
    "set `"FXHOUDINIMCP_AUTOSTART=$($env:FXHOUDINIMCP_AUTOSTART)`"",
    "cd /d `"$houdiniDir`""
)
if ($Visible) {
    $cmdParts += "start `"`" `"$houdini`""
} else {
    $cmdParts += "start /min `"`" `"$houdini`""
}
$cmdLine = $cmdParts -join ' && '
$launcher = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $cmdLine) -PassThru -WindowStyle Hidden
# `start` returns before houdini.exe is the current process; wait briefly
# and pick the newest houdini.exe as the reported pid.
Start-Sleep -Milliseconds 800
$houdiniProc = Get-Process -Name houdini -ErrorAction SilentlyContinue |
    Sort-Object StartTime -Descending |
    Select-Object -First 1
$reportedPid = if ($houdiniProc) { $houdiniProc.Id } else { $launcher.Id }
Write-Output "Started FXHoudiniMCP Windows Agents Houdini (pid $reportedPid, port $Port)."
Write-Output "Houdini root: $HoudiniRoot"
Write-Output "HOUDINI_USER_PREF_DIR=$env:HOUDINI_USER_PREF_DIR"
