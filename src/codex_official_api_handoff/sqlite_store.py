from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import strip_extended_prefix


@dataclass(frozen=True)
class ThreadRecord:
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def provider(self) -> str:
        return self.data["model_provider"]

    @property
    def rollout_path(self) -> Path:
        return strip_extended_prefix(self.data["rollout_path"])

    @property
    def title(self) -> str:
        return self.data.get("title") or self.id

    @property
    def cwd(self) -> str | None:
        return self.data.get("cwd")


class ThreadStore:
    def __init__(self, state_db: Path, readonly: bool = False) -> None:
        uri = "file:" + str(state_db) + "?mode=ro" if readonly else str(state_db)
        self.connection = sqlite3.connect(uri, uri=readonly)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def columns(self) -> list[str]:
        return [row[1] for row in self.connection.execute("pragma table_info(threads)")]

    def get(self, thread_id: str) -> ThreadRecord:
        row = self.connection.execute("select * from threads where id = ?", (thread_id,)).fetchone()
        if row is None:
            raise KeyError(f"Thread not found: {thread_id}")
        return ThreadRecord(dict(row))

    def provider_counts(self) -> Counter[str]:
        rows = self.connection.execute("select model_provider, count(*) from threads group by model_provider")
        return Counter({provider or "<null>": count for provider, count in rows})

    def active_by_provider(self, provider: str) -> list[ThreadRecord]:
        rows = self.connection.execute(
            "select * from threads where model_provider = ? and coalesce(archived, 0) = 0 order by updated_at desc",
            (provider,),
        )
        return [ThreadRecord(dict(row)) for row in rows]

    def insert_thread(self, record: ThreadRecord) -> None:
        columns = self.columns()
        placeholders = ",".join("?" for _ in columns)
        self.connection.execute(
            f"insert into threads ({','.join(columns)}) values ({placeholders})",
            [record.data.get(column) for column in columns],
        )

    def update_after_sync(self, target_id: str, source: ThreadRecord, now_seconds: int, now_ms: int) -> None:
        self.connection.execute(
            """
            update threads
            set updated_at = ?, updated_at_ms = ?, tokens_used = ?, preview = ?
            where id = ?
            """,
            (
                now_seconds,
                now_ms,
                source.data.get("tokens_used"),
                source.data.get("preview"),
                target_id,
            ),
        )

    def commit(self) -> None:
        self.connection.commit()

