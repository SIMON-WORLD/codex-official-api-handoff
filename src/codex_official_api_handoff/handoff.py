from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .backup import create_full_backup
from .config import read_model_provider
from .pairs import Pair, load_pairs, paired_ids, save_pairs
from .paths import CodexPaths
from .rollout import common_prefix, encrypted_count, load_jsonl, rewrite_extra_line
from .sqlite_store import ThreadRecord, ThreadStore


OFFICIAL_PROVIDER = "openai"


@dataclass
class SyncReport:
    pair: Pair
    direction: str
    extra_lines: int
    encrypted_removed: int
    applied: bool


def append_session_index(path: Path, thread_id: str, title: str) -> None:
    entry = {
        "id": thread_id,
        "thread_name": title,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def sync_pair(paths: CodexPaths, pair: Pair, apply: bool) -> SyncReport:
    store = ThreadStore(paths.state_db, readonly=not apply)
    try:
        official = store.get(pair.official)
        api = store.get(pair.api)
        official_lines = load_jsonl(official.rollout_path)
        api_lines = load_jsonl(api.rollout_path)

        api_common = common_prefix(api_lines, official_lines, api.id, official.id, official.provider)
        official_common = common_prefix(official_lines, api_lines, official.id, api.id, api.provider)

        if api_common == len(official_lines) and len(api_lines) > len(official_lines):
            source, target = api, official
            source_lines, target_lines = api_lines, official_lines
            common = api_common
            direction = "api-to-official"
        elif official_common == len(api_lines) and len(official_lines) > len(api_lines):
            source, target = official, api
            source_lines, target_lines = official_lines, api_lines
            common = official_common
            direction = "official-to-api"
        elif len(api_lines) == len(official_lines) and api_common == len(official_lines):
            return SyncReport(pair, "none", 0, 0, apply)
        else:
            raise RuntimeError(
                f"Conflict for pair {pair.name}: api_common={api_common}, official_common={official_common}, "
                f"api_lines={len(api_lines)}, official_lines={len(official_lines)}"
            )

        extra = source_lines[common:]
        rewritten = [
            line
            for line in (rewrite_extra_line(line, source.id, target.id, target.provider) for line in extra)
            if line is not None
        ]

        if apply and rewritten:
            with target.rollout_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.writelines(rewritten)
            now = int(time.time())
            store.update_after_sync(target.id, source, now, now * 1000)
            store.commit()
            append_session_index(paths.session_index, target.id, target.title)

        return SyncReport(pair, direction, len(extra), encrypted_count(extra), apply)
    finally:
        store.close()


def copy_thread_to_provider(paths: CodexPaths, source: ThreadRecord, target_provider: str, apply: bool) -> Pair | None:
    if not apply:
        return None

    store = ThreadStore(paths.state_db)
    try:
        new_id = str(uuid.uuid4())
        now = datetime.now()
        destination_dir = paths.sessions / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        destination = destination_dir / f"rollout-{now:%Y-%m-%dT%H-%M-%S}-{new_id}.jsonl"
        lines = []
        for line in load_jsonl(source.rollout_path):
            item = json.loads(line)
            if item.get("type") == "session_meta":
                payload = item.setdefault("payload", {})
                payload["id"] = new_id
                payload["model_provider"] = target_provider
            lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("".join(lines), encoding="utf-8")

        row = dict(source.data)
        row["id"] = new_id
        row["rollout_path"] = str(destination)
        row["model_provider"] = target_provider
        row["created_at"] = int(time.time())
        row["updated_at"] = row["created_at"]
        row["created_at_ms"] = row["created_at"] * 1000
        row["updated_at_ms"] = row["updated_at"] * 1000
        copied = ThreadRecord(row)
        store.insert_thread(copied)
        store.commit()
        append_session_index(paths.session_index, new_id, copied.title)
        if source.provider == OFFICIAL_PROVIDER:
            return Pair(name=source.id[:8], official=source.id, api=new_id, api_provider=target_provider, workspace=source.cwd)
        return Pair(name=new_id[:8], official=new_id, api=source.id, api_provider=source.provider, workspace=source.cwd)
    finally:
        store.close()


def is_automation_thread(record: ThreadRecord) -> bool:
    return record.title.startswith("Automation:")


def run_to(
    paths: CodexPaths,
    target: str,
    apply: bool,
    api_provider: str | None,
    backup_base: Path,
    copy_new: bool = False,
    include_automation: bool = False,
    show_new: bool = False,
) -> list[str]:
    messages: list[str] = []
    current_provider = read_model_provider(paths.config)
    inferred_api_provider = api_provider or (current_provider if current_provider and current_provider != OFFICIAL_PROVIDER else None)
    if not inferred_api_provider:
        raise RuntimeError("Cannot infer API provider. Pass --api-provider, for example --api-provider openai-chat-completions.")

    if apply:
        backup_root = create_full_backup(paths.home, backup_base)
        messages.append(f"backup={backup_root}")

    pairs = load_pairs(paths.pairs_file)
    for pair in pairs:
        report = sync_pair(paths, pair, apply=apply)
        messages.append(
            f"sync {pair.name}: direction={report.direction} extra={report.extra_lines} encrypted_removed={report.encrypted_removed}"
        )

    store = ThreadStore(paths.state_db, readonly=True)
    try:
        known = paired_ids(pairs)
        source_provider = OFFICIAL_PROVIDER if target == "api" else inferred_api_provider
        target_provider = inferred_api_provider if target == "api" else OFFICIAL_PROVIDER
        candidates = [record for record in store.active_by_provider(source_provider) if record.id not in known]
        if not include_automation:
            candidates = [record for record in candidates if not is_automation_thread(record)]
        messages.append(f"copy-new candidates from {source_provider} to {target_provider}: {len(candidates)}")
    finally:
        store.close()

    if copy_new and apply:
        for record in candidates:
            pair = copy_thread_to_provider(paths, record, target_provider, apply=True)
            if pair:
                pairs.append(pair)
                messages.append(f"copied {record.id} -> {pair.api if target == 'api' else pair.official}")
        save_pairs(paths.pairs_file, pairs)
    else:
        if candidates and not copy_new:
            messages.append("copy-new skipped; rerun with --copy-new to copy candidates")
        if show_new:
            for record in candidates[:20]:
                messages.append(f"would copy {record.id}: {record.title[:80]}")
            if len(candidates) > 20:
                messages.append(f"... and {len(candidates) - 20} more")

    return messages
