from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home)
    return Path.home() / ".codex"


def default_backup_base() -> Path:
    env_backup = os.environ.get("CODEX_HANDOFF_BACKUP_BASE")
    if env_backup:
        return Path(env_backup)
    return Path.home() / "codex-backups" / "codex-official-api-handoff"


@dataclass(frozen=True)
class CodexPaths:
    home: Path

    @property
    def config(self) -> Path:
        return self.home / "config.toml"

    @property
    def auth(self) -> Path:
        return self.home / "auth.json"

    @property
    def state_db(self) -> Path:
        return self.home / "state_5.sqlite"

    @property
    def session_index(self) -> Path:
        return self.home / "session_index.jsonl"

    @property
    def sessions(self) -> Path:
        return self.home / "sessions"

    @property
    def archived_sessions(self) -> Path:
        return self.home / "archived_sessions"

    @property
    def handoff_dir(self) -> Path:
        return self.home / "official-api-handoff"

    @property
    def pairs_file(self) -> Path:
        return self.handoff_dir / "pairs.json"


def strip_extended_prefix(path: str) -> Path:
    return Path(path.replace("\\\\?\\", ""))
