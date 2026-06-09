from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


def write_restore_script(backup_root: Path, codex_home: Path, target: Path) -> None:
    restore = backup_root / "restore-codex-backup.ps1"
    restore.write_text(
        f"""param([switch]$ConfirmRestore)
if (-not $ConfirmRestore) {{
  Write-Host 'This will restore {codex_home} from:'
  Write-Host '{target}'
  Write-Host 'Close Codex completely, then rerun with -ConfirmRestore.'
  exit 1
}}
$source = '{target}'
$target = '{codex_home}'
if (-not (Test-Path -LiteralPath $source)) {{ throw "Backup source not found: $source" }}
if (Get-Process -Name 'Codex','codex' -ErrorAction SilentlyContinue) {{ throw 'Codex appears to be running. Close Codex completely before restoring.' }}
if (Test-Path -LiteralPath $target) {{
  $quarantine = '{codex_home}.before-restore-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
  Move-Item -LiteralPath $target -Destination $quarantine
  Write-Host "Moved current .codex to $quarantine"
}}
robocopy $source $target /E /XJ /R:1 /W:1
if ($LASTEXITCODE -ge 8) {{ throw "robocopy restore failed with exit code $LASTEXITCODE" }}
Write-Host 'Restore complete.'
""",
        encoding="utf-8",
    )


def create_full_backup(codex_home: Path, backup_base: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = backup_base / stamp
    target = backup_root / ".codex"
    backup_root.mkdir(parents=True, exist_ok=True)

    robocopy = shutil.which("robocopy")
    if robocopy:
        result = subprocess.run(
            [robocopy, str(codex_home), str(target), "/E", "/XJ", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
            check=False,
        )
        if result.returncode >= 8:
            raise RuntimeError(f"robocopy failed with exit code {result.returncode}")
    else:
        shutil.copytree(codex_home, target, dirs_exist_ok=True, symlinks=True)

    write_restore_script(backup_root, codex_home, target)
    return backup_root


def create_quick_backup(codex_home: Path, backup_base: Path, files: list[Path]) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = backup_base / stamp
    target = backup_root / ".codex"
    target.mkdir(parents=True, exist_ok=True)
    for file_path in files:
        if not file_path.exists():
            continue
        try:
            relative = file_path.relative_to(codex_home)
        except ValueError:
            relative = Path(file_path.name)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, destination)
    write_restore_script(backup_root, codex_home, target)
    return backup_root
