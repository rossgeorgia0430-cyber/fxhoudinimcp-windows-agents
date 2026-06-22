[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$HoudiniVersion,
    [string]$HoudiniRoot,
    [string]$DocumentsPath,
    [string]$HoudiniPreferencesRoot,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$PackageName = 'fxhoudinimcp-windows-agents'
)

$ErrorActionPreference = 'Stop'

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
    if (-not [string]::IsNullOrWhiteSpace($env:HFS)) { $candidates.Add($env:HFS) }
    if (-not [string]::IsNullOrWhiteSpace($env:HOUDINI_ROOT)) { $candidates.Add($env:HOUDINI_ROOT) }

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

    throw 'Unable to locate Houdini. Pass -HoudiniVersion when Houdini is no longer installed.'
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
        throw "Could not determine the Houdini version. Pass -HoudiniVersion, for example 21.0."
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
$packagePath = Join-Path (Join-Path $preferencesDir 'packages') "$PackageName.json"
$backupPath = "$packagePath.backup"

if (Test-Path -LiteralPath $packagePath) {
    if ($PSCmdlet.ShouldProcess($packagePath, 'Remove FXHoudiniMCP Windows Agents package')) {
        Remove-Item -LiteralPath $packagePath -Force
        Write-Output "Removed Houdini package: $packagePath"
    }
} else {
    Write-Output "No installed package was found at: $packagePath"
}

if (Test-Path -LiteralPath $backupPath) {
    if ($PSCmdlet.ShouldProcess($packagePath, 'Restore previous same-name package')) {
        Move-Item -LiteralPath $backupPath -Destination $packagePath -Force
        Write-Output "Restored previous same-name package: $packagePath"
    }
}
