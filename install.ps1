param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root "src"

Write-Host "Installing codex-official-api-handoff command shims from:"
Write-Host $root

$scriptsDir = & $Python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
if ($LASTEXITCODE -ne 0 -or -not $scriptsDir) {
  throw "Cannot locate Python Scripts directory. Check Python command: $Python"
}
New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
$localBin = Join-Path $root "bin"
New-Item -ItemType Directory -Path $localBin -Force | Out-Null

$shortCmd = Join-Path $scriptsDir "codex-handoff.cmd"
$fullCmd = Join-Path $scriptsDir "codex-official-api-handoff.cmd"
$localShortCmd = Join-Path $localBin "codex-handoff.cmd"
$localFullCmd = Join-Path $localBin "codex-official-api-handoff.cmd"

@"
@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=$src;%PYTHONPATH%"
"$Python" -m codex_official_api_handoff.short_cli %*
"@ | Set-Content -LiteralPath $shortCmd -Encoding ascii
Copy-Item -LiteralPath $shortCmd -Destination $localShortCmd -Force

@"
@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=$src;%PYTHONPATH%"
"$Python" -m codex_official_api_handoff %*
"@ | Set-Content -LiteralPath $fullCmd -Encoding ascii
Copy-Item -LiteralPath $fullCmd -Destination $localFullCmd -Force

Write-Host ""
Write-Host "Command shims written to:"
Write-Host "  $shortCmd"
Write-Host "  $fullCmd"
Write-Host "  $localShortCmd"
Write-Host "  $localFullCmd"
Write-Host ""
Write-Host "Installed. You can now run:"
Write-Host "  codex-handoff api"
Write-Host "  codex-handoff official"
Write-Host ""
Write-Host "Daily workflow:"
Write-Host "  Before switching to API in cc-switch, run:       codex-handoff api"
Write-Host "  Before switching to official in cc-switch, run:  codex-handoff official"
Write-Host ""
Write-Host "Advanced:"
Write-Host "  codex-handoff connect api"
Write-Host "  codex-handoff connect official"
Write-Host "  codex-official-api-handoff check api"
Write-Host "  codex-official-api-handoff check official"
Write-Host ""
if (($env:Path -split ';') -notcontains $scriptsDir) {
  Write-Host "Note: Python Scripts directory is not in current PATH:"
  Write-Host "  $scriptsDir"
  Write-Host "If the command is not recognized, reopen PowerShell or add this directory to PATH."
}
