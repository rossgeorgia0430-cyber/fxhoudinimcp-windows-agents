[CmdletBinding()]
param(
    [int]$Port = 18100,
    [string]$HoudiniRoot
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'

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
$hython = Join-Path $HoudiniRoot 'bin\hython.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing fork-local Python: $python"
}
if (-not (Test-Path -LiteralPath $houdini)) {
    throw "Missing Houdini GUI executable: $houdini"
}
if (-not (Test-Path -LiteralPath $hython)) {
    throw "Missing Houdini hython executable: $hython"
}

function Invoke-ValidationStep {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "`n== $Name ==" -ForegroundColor Cyan
    # Houdini writes several non-fatal diagnostics to stderr (for example an
    # unavailable optional dialog script). Windows PowerShell turns those
    # into NativeCommandError records when ErrorActionPreference is Stop, even
    # when the process exits successfully. Native test commands are therefore
    # judged by their exit code; restore strict PowerShell errors afterwards.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Action
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

Push-Location $repo
try {
    Write-Host "Using Houdini root: $HoudiniRoot" -ForegroundColor Cyan
    Invoke-ValidationStep 'ruff (fatal errors)' { & $python -m ruff check --select F python tests houdini/scripts/python }
    Invoke-ValidationStep 'unit, schema, stdio MCP' { & $python -m pytest -q }
    Invoke-ValidationStep 'live Houdini integration and performance gate' { & $python tests/run_integration.py }
    $houdiniVersion = (& $hython -c 'import hou; print(hou.applicationVersionString())').Trim()
    if ([string]::IsNullOrWhiteSpace($houdiniVersion)) { $houdiniVersion = 'unknown' }
    $baseline = Join-Path $repo ".artifacts\visual-baselines\h$houdiniVersion-windows"
    $prefs = Join-Path ([System.IO.Path]::GetTempPath()) "fxhoudinimcp-windows-agents-h21-$PID"
    $previousPath = $env:HOUDINI_PATH
    $previousPort = $env:FXHOUDINIMCP_PORT
    $previousHoudiniPort = $env:HOUDINI_PORT
    $previousPrefs = $env:HOUDINI_USER_PREF_DIR
    # Houdini requires the literal __HVER__ token in this override; without
    # it, it ignores the path and loads the user's existing package set.
    $env:HOUDINI_USER_PREF_DIR = "$prefs\__HVER__"
    $env:HOUDINI_PATH = "$(Join-Path $repo 'houdini');&"
    $env:FXHOUDINIMCP_PORT = "$Port"
    $env:HOUDINI_PORT = "$Port"
    $env:FXHOUDINIMCP_AUTOSTART = '1'

    $gui = Start-Process -FilePath $houdini -WindowStyle Hidden -PassThru
    try {
        $deadline = (Get-Date).AddSeconds(150)
        do {
            Start-Sleep -Milliseconds 500
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api" -Method Post -ContentType 'application/x-www-form-urlencoded' -Body @{ json = '["mcp.health", [], {}]' } -UseBasicParsing
                if ($response.StatusCode -eq 200) { break }
            } catch {}
        } while ((Get-Date) -lt $deadline)
        if ($null -eq $response -or $response.StatusCode -ne 200) {
            throw "GUI Houdini did not expose mcp.health on port $Port within 150 seconds"
        }

        Invoke-ValidationStep 'HTTP bridge E2E (GUI main-thread path)' { & $python tests/integration/bridge_e2e.py --host 127.0.0.1 --port $Port }
        Invoke-ValidationStep 'stdio MCP E2E (GUI)' { & $python tests/integration/mcp_stdio_live_e2e.py --port $Port }
        Invoke-ValidationStep 'GUI visual baseline' { & $python tests/integration/gui_session_check.py --baseline-dir $baseline --update-baseline }
        Invoke-ValidationStep 'GUI visual comparison' { & $python tests/integration/gui_session_check.py --baseline-dir $baseline }
    } finally {
        if (-not $gui.HasExited) { Stop-Process -Id $gui.Id -Force }
        $env:HOUDINI_PATH = $previousPath
        $env:FXHOUDINIMCP_PORT = $previousPort
        $env:HOUDINI_PORT = $previousHoudiniPort
        $env:HOUDINI_USER_PREF_DIR = $previousPrefs
    }
} finally {
    Pop-Location
}
