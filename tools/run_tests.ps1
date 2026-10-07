<#
.SYNOPSIS
    Run every Holo-CAD test.

.DESCRIPTION
    All three suites run on FreeCAD's own Python with nothing installed,
    which is the same interpreter the addon runs on, so a pass here means
    a pass where it matters.

      test_holocad_server.py   the embedded HTTP and WebSocket server
      test_exporter.py         FreeCAD shapes to GLB, and the scale maths
      test_addon.py            the whole chain, FreeCAD object to downloaded GLB

    The exporter and addon suites need freecadcmd, because they build real
    FreeCAD documents.

.EXAMPLE
    .\tools\run_tests.ps1
#>

[CmdletBinding()]
param(
    [string]$FreeCadBin
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

function Find-FreeCadBin {
    if ($FreeCadBin) { return $FreeCadBin }
    $candidates = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not $base) { continue }
        $candidates += Get-ChildItem -Path $base -Filter "FreeCAD*" -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "bin" }
    }
    # @() matters: a single match would otherwise be a string, and indexing
    # a string hands back its first character.
    $found = @($candidates | Where-Object { Test-Path (Join-Path $_ "python.exe") } | Sort-Object -Descending)
    if ($found.Count -eq 0) {
        throw "Could not find FreeCAD. Pass -FreeCadBin with the path to its bin folder."
    }
    return $found[0]
}

$bin = Find-FreeCadBin
$python = Join-Path $bin "python.exe"
$freecadcmd = Join-Path $bin "freecadcmd.exe"
Write-Host "FreeCAD bin: $bin"
Write-Host ""

# The relay suites need aiohttp, so they run on the bridge venv. That is
# legitimate: the relay runs on a server, not inside FreeCAD, and is the one
# piece of this project allowed dependencies.
$venv = Join-Path $root "bridge\.venv\Scripts\python.exe"

$suites = @(
    @{ Name = "server";   Exe = $python;     Script = "tools\test_holocad_server.py" },
    @{ Name = "exporter"; Exe = $freecadcmd; Script = "tools\test_exporter.py" },
    @{ Name = "addon";    Exe = $freecadcmd; Script = "tools\test_addon.py" }
)
if (Test-Path $venv) {
    $suites += @{ Name = "relay"; Exe = $venv; Script = "tools\test_relay.py" }
    $suites += @{ Name = "relay end to end"; Exe = $venv; Script = "tools\test_relay_end_to_end.py" }
} else {
    Write-Host "skipping the relay suites: no bridge venv at $venv"
    Write-Host ""
}

$failed = @()
foreach ($suite in $suites) {
    Write-Host ("=" * 60)
    Write-Host $suite.Name
    Write-Host ("=" * 60)
    Push-Location $root
    try {
        $output = & $suite.Exe $suite.Script 2>&1 | Out-String
    } finally {
        Pop-Location
    }
    # freecadcmd exits 0 whatever the script returns, so the suites print a
    # final line and that is what decides the result.
    $output -split "`n" | Where-Object { $_ -match "^\s{2}(ok|FAIL)|^all checks passed|failure\(s\)" } | ForEach-Object {
        Write-Host $_.TrimEnd()
    }
    if ($output -notmatch "all checks passed") {
        $failed += $suite.Name
    }
    Write-Host ""
}

Write-Host ("=" * 60)
if ($failed.Count -gt 0) {
    Write-Host ("FAILED: " + ($failed -join ", "))
    exit 1
}
Write-Host "every suite passed"
exit 0
