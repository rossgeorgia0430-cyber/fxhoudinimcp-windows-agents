[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$HipFile,

    [Parameter(Mandatory = $true)]
    [string]$NodePath,

    [string[]]$ExpectedParm = @(),
    [string[]]$ExpectedRootParm = @(),
    [string]$HoudiniRoot
)

$ErrorActionPreference = 'Stop'

function Find-HoudiniRoot {
    param([string]$RequestedRoot)

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        $expandedRequestedRoot = [Environment]::ExpandEnvironmentVariables($RequestedRoot.Trim('"'))
        $requestedExecutable = [IO.Path]::Combine($expandedRequestedRoot, 'bin', 'hython.exe')
        if (Test-Path -LiteralPath $requestedExecutable) {
            return (Resolve-Path -LiteralPath $expandedRequestedRoot).Path
        }
        throw "No hython.exe exists below the requested root '$RequestedRoot'."
    }
    if (-not [string]::IsNullOrWhiteSpace($env:HFS)) {
        $candidates.Add($env:HFS)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:HOUDINI_ROOT)) {
        $candidates.Add($env:HOUDINI_ROOT)
    }

    $command = Get-Command hython.exe -ErrorAction SilentlyContinue
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
        $candidateExecutable = [IO.Path]::Combine($resolvedCandidate, 'bin', 'hython.exe')
        if (Test-Path -LiteralPath $candidateExecutable) {
            return (Resolve-Path -LiteralPath $resolvedCandidate).Path
        }
    }

    throw 'Unable to locate a Houdini installation. Install Houdini or pass -HoudiniRoot.'
}

if (-not (Test-Path -LiteralPath $HipFile)) {
    throw "HIP file does not exist: $HipFile"
}

$HoudiniRoot = Find-HoudiniRoot -RequestedRoot $HoudiniRoot
$hython = Join-Path $HoudiniRoot 'bin\hython.exe'
$hipPath = (Resolve-Path -LiteralPath $HipFile).Path
$tempScript = [IO.Path]::ChangeExtension([IO.Path]::GetTempFileName(), '.py')

$pythonSource = @'
import argparse
import json
import sys

import hou


def template_type_name(template):
    try:
        return template.type().name()
    except Exception:
        return str(template.type())


parser = argparse.ArgumentParser()
parser.add_argument("--hip", required=True)
parser.add_argument("--node", required=True)
parser.add_argument("--expected-parm", action="append", default=[])
parser.add_argument("--expected-root-parm", action="append", default=[])
args = parser.parse_args()

hou.hipFile.load(args.hip, suppress_save_prompt=True)
node = hou.node(args.node)
if node is None:
    print(json.dumps({"ok": False, "error": "missing node", "node": args.node}, indent=2))
    sys.exit(2)

parm_names = sorted(parm.name() for parm in node.parms())
spare_parm_names = sorted(parm.name() for parm in node.spareParms())
root_entries = []
for template in node.parmTemplateGroup().entries():
    root_entries.append(
        {
            "name": template.name(),
            "label": template.label(),
            "type": template_type_name(template),
        }
    )
root_entry_names = [entry["name"] for entry in root_entries]

missing_parms = [name for name in args.expected_parm if name not in parm_names]
missing_root_parms = [name for name in args.expected_root_parm if name not in root_entry_names]
result = {
    "ok": not missing_parms and not missing_root_parms,
    "hip": args.hip,
    "node": args.node,
    "missing_parms": missing_parms,
    "missing_root_parms": missing_root_parms,
    "spare_parms": spare_parm_names,
    "root_entries": root_entries,
}
print(json.dumps(result, indent=2))
sys.exit(0 if result["ok"] else 2)
'@

try {
    Set-Content -LiteralPath $tempScript -Value $pythonSource -Encoding UTF8
    $arguments = @(
        $tempScript,
        '--hip',
        $hipPath,
        '--node',
        $NodePath
    )
    foreach ($parm in $ExpectedParm) {
        if (-not [string]::IsNullOrWhiteSpace($parm)) {
            $arguments += @('--expected-parm', $parm)
        }
    }
    foreach ($parm in $ExpectedRootParm) {
        if (-not [string]::IsNullOrWhiteSpace($parm)) {
            $arguments += @('--expected-root-parm', $parm)
        }
    }

    & $hython @arguments
    exit $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $tempScript -ErrorAction SilentlyContinue
}
