param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Installing codex-official-api-handoff from:"
Write-Host $root
& $Python -m pip install -e $root

Write-Host ""
Write-Host "Installed. You can now run:"
Write-Host "  codex-handoff api"
Write-Host "  codex-handoff official"
Write-Host "  codex-handoff mirror api"
Write-Host "  codex-handoff mirror official"
