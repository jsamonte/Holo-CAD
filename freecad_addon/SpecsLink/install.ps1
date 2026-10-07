<#
.SYNOPSIS
    Link this addon into FreeCAD so the Holo-CAD workbench appears.

.DESCRIPTION
    Creates a directory junction from FreeCAD's Mod folder to this folder, so
    the addon runs from the repository and edits take effect on the next
    FreeCAD restart with nothing to copy.

    A junction needs no administrator rights, unlike a symbolic link.

    FreeCAD 1.1 keeps user data in a versioned folder, for example
    %APPDATA%\FreeCAD\v1-1, and does not create Mod until something does. The
    folder is found by asking FreeCAD itself rather than by guessing.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Uninstall
    .\install.ps1 -FreeCadPython "D:\FreeCAD\bin\python.exe"
#>

[CmdletBinding()]
param(
    [string]$FreeCadPython,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$source = $PSScriptRoot
$name = Split-Path $source -Leaf

function Find-FreeCadPython {
    if ($FreeCadPython) {
        if (-not (Test-Path $FreeCadPython)) {
            throw "No python.exe at $FreeCadPython"
        }
        return $FreeCadPython
    }
    $candidates = @()
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not $root) { continue }
        $candidates += Get-ChildItem -Path $root -Filter "FreeCAD*" -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "bin\python.exe" }
    }
    # @() matters: with a single match PowerShell unwraps the pipeline to a
    # string, and indexing a string returns its first character, so the path
    # silently becomes "C".
    $found = @($candidates | Where-Object { Test-Path $_ } | Sort-Object -Descending)
    if ($found.Count -eq 0) {
        throw "Could not find FreeCAD. Pass -FreeCadPython with the path to its bin\python.exe"
    }
    return $found[0]
}

function Get-UserModPath($python) {
    # FreeCAD is the authority on its own user folder, which is versioned.
    $script = @'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(sys.executable)))
import FreeCAD
print(os.path.join(FreeCAD.getUserAppDataDir(), "Mod"))
'@
    $temp = [System.IO.Path]::GetTempFileName() + ".py"
    Set-Content -Path $temp -Value $script -Encoding utf8
    try {
        $out = & $python $temp 2>$null
    } finally {
        Remove-Item $temp -ErrorAction SilentlyContinue
    }
    $line = ($out | Where-Object { $_ -match "Mod$" } | Select-Object -Last 1)
    if (-not $line) { throw "FreeCAD did not report its user folder" }
    return $line.Trim()
}

$python = Find-FreeCadPython
Write-Host "FreeCAD python : $python"
$modPath = Get-UserModPath $python
Write-Host "FreeCAD Mod    : $modPath"
$target = Join-Path $modPath $name

if ($Uninstall) {
    if (Test-Path $target) {
        $item = Get-Item $target -Force
        if ($item.LinkType) {
            $item.Delete()
            Write-Host "Removed the junction at $target"
        } else {
            throw "$target is a real folder, not a junction. Not touching it."
        }
    } else {
        Write-Host "Nothing installed at $target"
    }
    Write-Host "Restart FreeCAD to drop the workbench."
    return
}

if (-not (Test-Path $modPath)) {
    New-Item -ItemType Directory -Path $modPath -Force | Out-Null
    Write-Host "Created $modPath"
}

if (Test-Path $target) {
    $item = Get-Item $target -Force
    if ($item.LinkType -eq "Junction") {
        $existing = $item.Target | Select-Object -First 1
        if ($existing -eq $source) {
            Write-Host "Already linked. Nothing to do."
            Write-Host "Restart FreeCAD and pick Holo-CAD from the workbench list."
            return
        }
        $item.Delete()
        Write-Host "Replaced a junction that pointed at $existing"
    } else {
        throw "$target already exists and is not a junction. Move it aside first."
    }
}

New-Item -ItemType Junction -Path $target -Target $source | Out-Null
Write-Host ""
Write-Host "Linked $target"
Write-Host "   -> $source"
Write-Host ""
Write-Host "Restart FreeCAD, then pick Holo-CAD from the workbench dropdown."
Write-Host "The Report view prints the address to paste into the lens."
