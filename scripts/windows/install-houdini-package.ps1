[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$HoudiniVersion,
    [int]$Port = 18100,
    [string]$HoudiniRoot,
    [string]$DocumentsPath,
    [string]$HoudiniPreferencesRoot,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$PackageName = 'fxhoudinimcp-codex-windows'
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
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate.Trim('"'))
        $candidateExecutable = [IO.Path]::Combine($expanded, 'bin', 'houdini.exe')
        if (Test-Path -LiteralPath $candidateExecutable) {
            return (Resolve-Path -LiteralPath $expanded).Path
        }
    }

    throw 'Unable to locate Houdini. Install Houdini, set HFS/HOUDINI_ROOT, or pass -HoudiniRoot or -HoudiniVersion.'
}

function Get-DocumentsDirectory {
    $documents = [Environment]::GetFolderPath('MyDocuments')
    if ([string]::IsNullOrWhiteSpace($documents)) {
        $shellFolders = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
        $documents = (Get-ItemProperty -LiteralPath $shellFolders -Name Personal -ErrorAction SilentlyContinue).Personal
    }
    if ([string]::IsNullOrWhiteSpace($documents)) {
        throw 'Windows did not report a Documents known-folder path. Pass -HoudiniPreferencesRoot explicitly.'
    }
    return [Environment]::ExpandEnvironmentVariables($documents)
}

if ([string]::IsNullOrWhiteSpace($HoudiniVersion)) {
    $detectedRoot = Find-HoudiniRoot -RequestedRoot $HoudiniRoot
    $versionMatch = [regex]::Match((Split-Path -Leaf $detectedRoot), '(\d+)\.(\d+)')
    if (-not $versionMatch.Success) {
        $productVersion = (Get-Item -LiteralPath (Join-Path $detectedRoot 'bin\houdini.exe')).VersionInfo.ProductVersion
        $versionMatch = [regex]::Match($productVersion, '(\d+)\.(\d+)')
    }
    if (-not $versionMatch.Success) {
        throw "Could not determine the Houdini major/minor version from '$detectedRoot'. Pass -HoudiniVersion, for example 21.0."
    }
    $HoudiniVersion = "$($versionMatch.Groups[1].Value).$($versionMatch.Groups[2].Value)"
}

if ($HoudiniVersion -notmatch '^\d+\.\d+$') {
    throw "HoudiniVersion must be major.minor, for example '21.0'. Received '$HoudiniVersion'."
}

$preferencesDir = if (-not [string]::IsNullOrWhiteSpace($HoudiniPreferencesRoot)) {
    [Environment]::ExpandEnvironmentVariables($HoudiniPreferencesRoot)
} elseif (-not [string]::IsNullOrWhiteSpace($DocumentsPath)) {
    Join-Path ([Environment]::ExpandEnvironmentVariables($DocumentsPath)) "houdini$HoudiniVersion"
} else {
    Join-Path (Get-DocumentsDirectory) "houdini$HoudiniVersion"
}
$packageDir = Join-Path $preferencesDir 'packages'
$packagePath = Join-Path $packageDir "$PackageName.json"
$backupPath = "$packagePath.backup"
$houdiniDir = (Join-Path $repo 'houdini').Replace('\', '/')

$package = [ordered]@{
    enable = $true
    env = @(
        [ordered]@{
            FXHOUDINIMCP_CODEX_WINDOWS = [ordered]@{
                value = $houdiniDir
                method = 'replace'
            }
        },
        [ordered]@{
            FXHOUDINIMCP_PORT = [ordered]@{
                value = "$Port"
                method = 'replace'
            }
        }
    )
    path = '$FXHOUDINIMCP_CODEX_WINDOWS'
}
$json = $package | ConvertTo-Json -Depth 8

$installed = $false
if ($PSCmdlet.ShouldProcess($packagePath, 'Install project-local Houdini package')) {
    New-Item -ItemType Directory -Force -Path $packageDir | Out-Null

    if (Test-Path -LiteralPath $packagePath) {
        $existing = [IO.File]::ReadAllText($packagePath, [Text.Encoding]::UTF8)
        if ($existing.Trim() -ne $json.Trim() -and -not (Test-Path -LiteralPath $backupPath)) {
            Copy-Item -LiteralPath $packagePath -Destination $backupPath
            Write-Output "Backed up the previous same-name package: $backupPath"
        }
    }

    $temporaryPath = "$packagePath.tmp-$PID"
    try {
        [IO.File]::WriteAllText($temporaryPath, $json, (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporaryPath -Destination $packagePath -Force
        $installed = $true
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

$packagePreferencesPath = Join-Path $preferencesDir 'package.pref'
if (Test-Path -LiteralPath $packagePreferencesPath) {
    $packagePreferences = Get-Content -LiteralPath $packagePreferencesPath -Raw
    if ($packagePreferences -match 'pkg\.autoload\s*:=\s*0\s*;') {
        Write-Warning "Houdini package auto-loading is disabled in '$packagePreferencesPath'. This installer intentionally did not modify the global preference."
    }
}

if ($installed) {
    Write-Output "Installed Houdini package: $packagePath"
} else {
    Write-Output "Package installation was not applied: $packagePath"
}
Write-Output "Houdini preferences: $preferencesDir"
Write-Output "FXHoudiniMCP source: $houdiniDir"
Write-Output "FXHoudiniMCP port: $Port"
Write-Output "Uninstall with: '$PSScriptRoot\uninstall-houdini-package.ps1' -HoudiniVersion $HoudiniVersion"
