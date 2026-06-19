from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backup import create_full_backup, create_quick_backup
from .config import read_model_provider
from .pairs import Pair, load_pairs, paired_ids, save_pairs
from .paths import CodexPaths
from .rollout import common_prefix, encrypted_count, load_jsonl, rewrite_extra_line, rewrite_rollout_for_target, write_jsonl
from .sqlite_store import ThreadRecord, ThreadStore


OFFICIAL_PROVIDER = "openai"


@dataclass
class SyncReport:
    pair: Pair
    direction: str
    extra_lines: int
    encrypted_removed: int
    applied: bool
    title: str | None = None
    conflict_resolved: bool = False


@dataclass
class MirrorPlan:
    source_provider: str
    target_provider: str
    visible: list[ThreadRecord]
    paired: list[ThreadRecord]
    to_copy: list[ThreadRecord]
    skipped_automation: int = 0
    skipped_test: int = 0
    skipped_workspace: int = 0


@dataclass
class MirrorDiff:
    source_provider: str
    target_provider: str
    source_count: int
    target_count: int
    source_archived_count: int
    target_archived_count: int
    missing_in_target: list[ThreadRecord]
    extra_in_target: list[ThreadRecord]
    paired_source_archived_extras: list[ThreadRecord]
    source_active_target_archived: list[tuple[ThreadRecord, ThreadRecord]]
    source_archived_target_active: list[tuple[ThreadRecord, ThreadRecord]]
    archived_missing_in_target: list[ThreadRecord]
    archived_extra_in_target: list[ThreadRecord]
    title_mismatches: list[tuple[ThreadRecord, ThreadRecord, str, str]]
    order_mismatches: list[tuple[int, ThreadRecord | None, ThreadRecord | None]]
    timestamp_mismatches: list[tuple[ThreadRecord, ThreadRecord, int | None, int | None]]
    paired_source_count: int

    def has_problems(self) -> bool:
        return bool(
            self.missing_in_target
            or self.extra_in_target
            or self.source_active_target_archived
            or self.source_archived_target_active
            or self.title_mismatches
            or self.order_mismatches
            or self.timestamp_mismatches
        )

    def has_archive_mismatch(self) -> bool:
        return bool(self.source_active_target_archived or self.source_archived_target_active)

    def is_pending_handoff(self) -> bool:
        return bool(self.missing_in_target) and not self.extra_in_target and not self.has_archive_mismatch()

    def is_target_ahead_only(self) -> bool:
        return bool(self.extra_in_target) and not self.missing_in_target and not self.has_archive_mismatch()


def provider_label(provider: str) -> str:
    return "官方" if provider == OFFICIAL_PROVIDER else "API"


GENERIC_TITLES = {"你好", "新聊天", "New chat", "Untitled"}


def is_generic_title(title: str | None) -> bool:
    if not title:
        return True
    normalized = title.strip()
    return normalized in GENERIC_TITLES or len(normalized) <= 2


def is_likely_manual_title(title: str | None) -> bool:
    if is_generic_title(title):
        return False
    text = title.strip()
    if text.startswith("Automation:"):
        return False
    if len(text) <= 40:
        return True
    if len(text) >= 80:
        return False
    prefixes = ("01 ", "02 ", "03 ", "04 ", "05 ", "06 ", "07 ", "08 ", "09 ")
    return text.startswith(prefixes)


def is_numbered_title(title: str | None) -> bool:
    if not title:
        return False
    text = title.strip()
    return len(text) >= 3 and text[:2].isdigit() and text[2] in {" ", "-", "_", "、", "."}


def mirror_title(source_title: str, target_title: str | None) -> str:
    if target_title and is_numbered_title(target_title) and not is_numbered_title(source_title):
        return target_title
    if target_title and is_likely_manual_title(target_title) and not is_likely_manual_title(source_title):
        return target_title
    if is_generic_title(source_title) and target_title and not is_generic_title(target_title):
        return target_title
    return source_title


def direction_label(direction: str) -> str:
    labels = {
        "none": "无需同步",
        "api-to-official": "API -> 官方",
        "official-to-api": "官方 -> API",
    }
    return labels.get(direction, direction)


def display_title(title: str | None, limit: int = 80) -> str | None:
    if not title:
        return None
    text = " ".join(title.split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def display_record(record: ThreadRecord, limit: int = 80) -> str:
    return f"{record.id}  {display_title(record.title, limit) or record.id}"


def report_sync_message(report: SyncReport) -> str:
    if report.direction == "none":
        short = display_title(report.title)
        title = f"，标题：{short}" if short else ""
        return f"会话 {report.pair.name}：两边已经一致{title}。"
    source = "API" if report.direction == "api-to-official" else "官方"
    target = "官方" if report.direction == "api-to-official" else "API"
    encrypted = f"，已忽略 provider 加密片段 {report.encrypted_removed} 个" if report.encrypted_removed else ""
    short = display_title(report.title)
    title = f"，标题：{short}" if short else ""
    if report.conflict_resolved:
        return (
            f"会话 {report.pair.name}：检测到两侧内容分叉，已以{source}侧为主线重建{target}副本"
            f"（旧副本已包含在本次完整备份中）{encrypted}{title}。"
        )
    return f"会话 {report.pair.name}：发现{source}侧有新增内容记录 {report.extra_lines} 条，准备同步到{target}{encrypted}{title}。"


def session_index_timestamp(updated_at: int | float | str | None = None) -> str:
    if isinstance(updated_at, str) and updated_at.strip():
        return updated_at.strip()
    if isinstance(updated_at, (int, float)):
        seconds = updated_at / 1000 if updated_at > 10_000_000_000 else updated_at
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_session_index(path: Path, thread_id: str, title: str, updated_at: int | float | str | None = None) -> None:
    entry = {
        "id": thread_id,
        "thread_name": title,
        "updated_at": session_index_timestamp(updated_at),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_session_index_titles(path: Path) -> dict[str, str]:
    titles: dict[str, str] = {}
    if not path.exists():
        return titles
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            thread_id = item.get("id")
            title = item.get("thread_name")
            if isinstance(thread_id, str) and isinstance(title, str) and title.strip():
                titles[thread_id] = title.strip()
    return titles


def record_display_title(record: ThreadRecord, index_titles: dict[str, str] | None = None) -> str:
    if index_titles and record.id in index_titles:
        return index_titles[record.id]
    return record.title


def active_rollout_path(paths: CodexPaths, rollout_path: Path) -> Path:
    name = rollout_path.name
    if name.startswith("rollout-") and len(name) >= 18:
        date = name[len("rollout-") : len("rollout-") + 10]
        if len(date) == 10 and date[4] == "-" and date[7] == "-":
            year, month, day = date.split("-")
            return paths.sessions / year / month / day / name
    return paths.sessions / name


def archived_rollout_path(paths: CodexPaths, rollout_path: Path) -> Path:
    return paths.archived_sessions / rollout_path.name


def existing_rollout_path(paths: CodexPaths, record: ThreadRecord) -> Path:
    rollout_path = record.rollout_path
    if rollout_path.exists():
        return rollout_path
    archived_path = archived_rollout_path(paths, rollout_path)
    if archived_path.exists():
        return archived_path
    active_path = active_rollout_path(paths, rollout_path)
    if active_path.exists():
        return active_path
    return rollout_path


def repair_rollout_path_if_needed(store: ThreadStore, thread_id: str, current: Path, actual: Path) -> None:
    if actual != current:
        store.update_rollout_path(thread_id, actual)


def relocate_rollout_file(paths: CodexPaths, record: ThreadRecord, archived: bool) -> Path:
    current = existing_rollout_path(paths, record)
    desired = archived_rollout_path(paths, current) if archived else active_rollout_path(paths, current)
    if current == desired:
        return desired
    if desired.exists():
        return desired
    if not current.exists():
        return current
    desired.parent.mkdir(parents=True, exist_ok=True)
    current.replace(desired)
    return desired


def sync_archive_state(paths: CodexPaths, store: ThreadStore, thread_id: str, archived: bool) -> None:
    record = store.get(thread_id)
    rollout_path = relocate_rollout_file(paths, record, archived)
    if rollout_path != record.rollout_path:
        store.update_rollout_path(thread_id, rollout_path)
    store.update_archived(thread_id, archived)


def preferred_title(pair: Pair, official: ThreadRecord, api: ThreadRecord, forced_title: str | None = None) -> str:
    if forced_title:
        pair.title = forced_title
        return forced_title
    if pair.title_mode == "locked" and pair.title:
        return pair.title
    if pair.title:
        if official.title == api.title:
            pair.title = official.title
            return official.title
        if official.title == pair.title and api.title != pair.title:
            pair.title = api.title
            return api.title
        if api.title == pair.title and official.title != pair.title:
            pair.title = official.title
            return official.title
        raise RuntimeError(
            f"Title conflict for pair {pair.name}: official={official.title!r}, api={api.title!r}, "
            f"last_synced={pair.title!r}"
        )
    official_updated = official.data.get("updated_at") or 0
    api_updated = api.data.get("updated_at") or 0
    if api_updated >= official_updated and api.title:
        pair.title = api.title
        return api.title
    if official.title:
        pair.title = official.title
        return official.title
    pair.title = api.title or official.title
    return pair.title


def sync_pair_metadata(
    store: ThreadStore,
    pair: Pair,
    official: ThreadRecord,
    api: ThreadRecord,
    title: str | None,
    archived: bool | None = None,
) -> None:
    if title:
        store.update_title(official.id, title)
        store.update_title(api.id, title)
    if archived is not None:
        store.update_archived(official.id, archived)
        store.update_archived(api.id, archived)
    elif official.archived != api.archived:
        merged_archived = official.archived or api.archived
        store.update_archived(official.id, merged_archived)
        store.update_archived(api.id, merged_archived)


def sync_pair(
    paths: CodexPaths,
    pair: Pair,
    apply: bool,
    forced_title: str | None = None,
    forced_archived: bool | None = None,
    forced_source_id: str | None = None,
) -> SyncReport:
    store = ThreadStore(paths.state_db, readonly=not apply)
    try:
        official = store.get(pair.official)
        api = store.get(pair.api)
        title = preferred_title(pair, official, api, forced_title=forced_title)
        official_path = existing_rollout_path(paths, official)
        api_path = existing_rollout_path(paths, api)
        official_lines = load_jsonl(official_path)
        api_lines = load_jsonl(api_path)

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
            if apply and title:
                repair_rollout_path_if_needed(store, official.id, official.rollout_path, official_path)
                repair_rollout_path_if_needed(store, api.id, api.rollout_path, api_path)
                sync_pair_metadata(store, pair, official, api, title, archived=forced_archived)
                store.commit()
                append_session_index(paths.session_index, official.id, title, official.data.get("updated_at_ms") or official.data.get("updated_at"))
                append_session_index(paths.session_index, api.id, title, api.data.get("updated_at_ms") or api.data.get("updated_at"))
            return SyncReport(pair, "none", 0, 0, apply, title=title)
        else:
            if forced_source_id not in {official.id, api.id}:
                raise RuntimeError(
                    f"Conflict for pair {pair.name}: api_common={api_common}, official_common={official_common}, "
                    f"api_lines={len(api_lines)}, official_lines={len(official_lines)}"
                )
            source = official if forced_source_id == official.id else api
            target = api if source.id == official.id else official
            source_lines = official_lines if source.id == official.id else api_lines
            target_lines = api_lines if target.id == api.id else official_lines
            direction = "official-to-api" if source.id == official.id else "api-to-official"
            rewritten = rewrite_rollout_for_target(source_lines, source.id, target.id, target.provider)
            if apply:
                target_path = api_path if target.id == api.id else official_path
                temporary_path = target_path.with_name(target_path.name + ".handoff-tmp")
                write_jsonl(temporary_path, rewritten)
                temporary_path.replace(target_path)
                source_seconds = source.data.get("updated_at") or int(time.time())
                source_ms = source.data.get("updated_at_ms") or int(source_seconds) * 1000
                repair_rollout_path_if_needed(store, official.id, official.rollout_path, official_path)
                repair_rollout_path_if_needed(store, api.id, api.rollout_path, api_path)
                store.update_after_sync(target.id, source, int(source_seconds), int(source_ms))
                sync_pair_metadata(store, pair, official, api, title, archived=forced_archived)
                store.commit()
                append_session_index(paths.session_index, target.id, title or target.title, source_ms)
                append_session_index(paths.session_index, source.id, title or source.title, source_ms)
            return SyncReport(
                pair,
                direction,
                len(source_lines),
                encrypted_count(source_lines),
                apply,
                title=title,
                conflict_resolved=True,
            )

        extra = source_lines[common:]
        rewritten = [
            line
            for line in (rewrite_extra_line(line, source.id, target.id, target.provider) for line in extra)
            if line is not None
        ]

        if apply:
            if rewritten:
                target_path = official_path if target.id == official.id else api_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with target_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.writelines(rewritten)
            source_seconds = source.data.get("updated_at") or int(time.time())
            source_ms = source.data.get("updated_at_ms") or int(source_seconds) * 1000
            repair_rollout_path_if_needed(store, official.id, official.rollout_path, official_path)
            repair_rollout_path_if_needed(store, api.id, api.rollout_path, api_path)
            if rewritten:
                store.update_after_sync(target.id, source, int(source_seconds), int(source_ms))
            sync_pair_metadata(store, pair, official, api, title, archived=forced_archived)
            store.commit()
            append_session_index(paths.session_index, target.id, title or target.title, source_ms)
            append_session_index(paths.session_index, source.id, title or source.title, source_ms)

        return SyncReport(pair, direction, len(extra), encrypted_count(extra), apply, title=title)
    finally:
        store.close()


def copy_thread_to_provider(
    paths: CodexPaths,
    source: ThreadRecord,
    target_provider: str,
    apply: bool,
    title: str | None = None,
) -> Pair | None:
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
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
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
        if title:
            row["title"] = title
        copied = ThreadRecord(row)
        store.insert_thread(copied)
        store.commit()
        append_session_index(
            paths.session_index,
            new_id,
            title or copied.title,
            source.data.get("updated_at_ms") or source.data.get("updated_at"),
        )
        if source.provider == OFFICIAL_PROVIDER:
            return Pair(name=source.id[:8], official=source.id, api=new_id, api_provider=target_provider, workspace=source.cwd)
        return Pair(name=new_id[:8], official=new_id, api=source.id, api_provider=source.provider, workspace=source.cwd)
    finally:
        store.close()


def pair_existing_threads(source: ThreadRecord, target_record: ThreadRecord, source_provider: str, target_provider: str) -> Pair:
    if source_provider == OFFICIAL_PROVIDER:
        return Pair(
            name=source.id[:8],
            official=source.id,
            api=target_record.id,
            api_provider=target_provider,
            workspace=source.cwd,
            title=source.title,
        )
    return Pair(
        name=target_record.id[:8],
        official=target_record.id,
        api=source.id,
        api_provider=source.provider,
        workspace=source.cwd,
        title=source.title,
    )


def match_existing_target_copies(
    sources: list[ThreadRecord],
    target_records: list[ThreadRecord],
) -> tuple[list[tuple[ThreadRecord, ThreadRecord]], list[ThreadRecord]]:
    remaining_targets = list(target_records)
    matched: list[tuple[ThreadRecord, ThreadRecord]] = []
    unmatched_sources: list[ThreadRecord] = []
    for source in sources:
        index = next(
            (
                i
                for i, target in enumerate(remaining_targets)
                if target.title == source.title and target.cwd == source.cwd
            ),
            None,
        )
        if index is None:
            unmatched_sources.append(source)
            continue
        matched.append((source, remaining_targets.pop(index)))
    return matched, unmatched_sources


def copy_one(
    paths: CodexPaths,
    source_id: str,
    target: str,
    apply: bool,
    api_provider: str | None,
    backup_base: Path,
    name: str | None = None,
) -> list[str]:
    messages: list[str] = []
    current_provider = read_model_provider(paths.config)
    inferred_api_provider = api_provider or (current_provider if current_provider and current_provider != OFFICIAL_PROVIDER else None)
    if not inferred_api_provider:
        raise RuntimeError("Cannot infer API provider. Pass --api-provider, for example --api-provider openai-chat-completions.")

    store = ThreadStore(paths.state_db, readonly=True)
    try:
        source = store.get(source_id)
        target_provider = inferred_api_provider if target == "api" else OFFICIAL_PROVIDER
        messages.append(f"source={source.id} provider={source.provider}")
        messages.append(f"target_provider={target_provider}")
        messages.append(f"title={source.title[:100]}")
        if source.provider == target_provider:
            raise RuntimeError(f"Source is already in target provider: {target_provider}")
    finally:
        store.close()

    if not apply:
        messages.append("dry_run=true")
        messages.append("rerun with --apply to copy this one thread")
        return messages

    backup_root = create_full_backup(paths.home, backup_base)
    messages.append(f"backup={backup_root}")
    pair = copy_thread_to_provider(paths, source, target_provider, apply=True)
    if pair is None:
        raise RuntimeError("copy_thread_to_provider returned no pair")
    if name:
        pair.name = name
    pairs = load_pairs(paths.pairs_file)
    pairs.append(pair)
    save_pairs(paths.pairs_file, pairs)
    messages.append(f"copied official={pair.official} api={pair.api} pair={pair.name}")
    return messages


def is_automation_thread(record: ThreadRecord) -> bool:
    first_message = record.data.get("first_user_message") or ""
    return record.title.lstrip().startswith("Automation:") or first_message.lstrip().startswith("Automation:")


def is_test_thread(record: ThreadRecord) -> bool:
    text = f"{record.title}\n{record.id}".lower()
    markers = ["sync_test", "测试", "debug"]
    return any(marker in text for marker in markers)


def known_workspaces(pairs: list[Pair]) -> set[str]:
    return {pair.workspace for pair in pairs if pair.workspace}


def run_to(
    paths: CodexPaths,
    target: str,
    apply: bool,
    api_provider: str | None,
    backup_base: Path,
    copy_new: bool = False,
    include_automation: bool = False,
    show_new: bool = False,
    backup_mode: str = "full",
) -> list[str]:
    messages: list[str] = []
    current_provider = read_model_provider(paths.config)
    pairs = load_pairs(paths.pairs_file)
    pair_providers = sorted({pair.api_provider for pair in pairs if pair.api_provider})
    inferred_api_provider = api_provider or (current_provider if current_provider and current_provider != OFFICIAL_PROVIDER else None)
    if not inferred_api_provider and len(pair_providers) == 1:
        inferred_api_provider = pair_providers[0]
    if not inferred_api_provider:
        raise RuntimeError(
            "Cannot infer API provider. Pass --api-provider, for example --api-provider openai-chat-completions."
        )

    if apply:
        if backup_mode == "quick":
            backup_files = collect_quick_backup_files(paths, pairs)
            backup_root = create_quick_backup(paths.home, backup_base, backup_files)
            messages.append(f"备份模式=quick")
        else:
            backup_root = create_full_backup(paths.home, backup_base)
            messages.append(f"备份模式=full")
        messages.append(f"备份位置={backup_root}")

    for pair in pairs:
        report = sync_pair(paths, pair, apply=apply)
        messages.append(report_sync_message(report))
    if apply:
        save_pairs(paths.pairs_file, pairs)

    store = ThreadStore(paths.state_db, readonly=True)
    try:
        known = paired_ids(pairs)
        source_provider = OFFICIAL_PROVIDER if target == "api" else inferred_api_provider
        target_provider = inferred_api_provider if target == "api" else OFFICIAL_PROVIDER
        candidates = [record for record in store.active_by_provider(source_provider) if record.id not in known]
        if not include_automation:
            candidates = [record for record in candidates if not is_automation_thread(record)]
        messages.append(f"另有 {len(candidates)} 条{provider_label(source_provider)}会话尚未接入 handoff，本次已跳过。")
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
            messages.append("如需接入更多会话，请运行交互式 connect 命令。")
        if show_new:
            for record in candidates[:20]:
                messages.append(f"would copy {record.id}: {record.title[:80]}")
            if len(candidates) > 20:
                messages.append(f"... and {len(candidates) - 20} more")

    return messages


def collect_quick_backup_files(paths: CodexPaths, pairs: list[Pair]) -> list[Path]:
    files = [paths.state_db, paths.session_index, paths.pairs_file]
    store = ThreadStore(paths.state_db, readonly=True)
    try:
        for pair in pairs:
            for thread_id in (pair.official, pair.api):
                try:
                    files.append(store.get(thread_id).rollout_path)
                except KeyError:
                    continue
    finally:
        store.close()
    seen: set[Path] = set()
    unique = []
    for file_path in files:
        if file_path not in seen:
            seen.add(file_path)
            unique.append(file_path)
    return unique


def set_pair_title(paths: CodexPaths, pair_name: str, title: str, apply: bool) -> list[str]:
    pairs = load_pairs(paths.pairs_file)
    pair = next((item for item in pairs if item.name == pair_name), None)
    if not pair:
        raise RuntimeError(f"Pair not found: {pair_name}")
    messages = [f"会话 {pair.name}：准备统一标题为：{title}"]
    if not apply:
        messages.append("dry_run=true")
        return messages
    pair.title = title
    save_pairs(paths.pairs_file, pairs)
    store = ThreadStore(paths.state_db)
    try:
        store.update_title(pair.official, title)
        store.update_title(pair.api, title)
        store.commit()
    finally:
        store.close()
    store = ThreadStore(paths.state_db, readonly=True)
    try:
        official = store.get(pair.official)
        api = store.get(pair.api)
    finally:
        store.close()
    append_session_index(paths.session_index, pair.official, title, official.data.get("updated_at_ms") or official.data.get("updated_at"))
    append_session_index(paths.session_index, pair.api, title, api.data.get("updated_at_ms") or api.data.get("updated_at"))
    messages.append("标题已更新。")
    return messages


def refresh_session_index(paths: CodexPaths, apply: bool, backup_base: Path | None = None) -> list[str]:
    pairs = load_pairs(paths.pairs_file)
    messages = [f"准备刷新已接入会话的左侧列表标题索引：{len(pairs)} 对"]
    if not pairs:
        return messages

    if apply:
        if backup_base is None:
            raise RuntimeError("backup_base is required when apply=True")
        backup_root = create_quick_backup(paths.home, backup_base, [paths.state_db, paths.session_index, paths.pairs_file])
        messages.append("备份模式=quick")
        messages.append(f"备份位置={backup_root}")

    store = ThreadStore(paths.state_db, readonly=not apply)
    try:
        refreshed = 0
        for pair in pairs:
            official = store.get(pair.official)
            api = store.get(pair.api)
            title = preferred_title(pair, official, api)
            if apply:
                sync_pair_metadata(store, pair, official, api, title)
                append_session_index(paths.session_index, official.id, title, official.data.get("updated_at_ms") or official.data.get("updated_at"))
                append_session_index(paths.session_index, api.id, title, api.data.get("updated_at_ms") or api.data.get("updated_at"))
            refreshed += 2
            messages.append(f"会话 {pair.name}：{title}")
        if apply:
            store.commit()
            save_pairs(paths.pairs_file, pairs)
            messages.append(f"已刷新索引记录：{refreshed} 条")
        else:
            messages.append("dry_run=true")
    finally:
        store.close()
    return messages


def mirror_plan(
    paths: CodexPaths,
    target: str,
    api_provider: str | None = None,
    include_automation: bool = False,
    include_tests: bool = False,
    current_workspace_only: bool = False,
) -> MirrorPlan:
    current_provider = read_model_provider(paths.config)
    pairs = load_pairs(paths.pairs_file)
    pair_providers = sorted({pair.api_provider for pair in pairs if pair.api_provider})
    inferred_api_provider = api_provider or (current_provider if current_provider and current_provider != OFFICIAL_PROVIDER else None)
    if not inferred_api_provider and len(pair_providers) == 1:
        inferred_api_provider = pair_providers[0]
    if not inferred_api_provider:
        raise RuntimeError("Cannot infer API provider for mirror mode.")

    source_provider = OFFICIAL_PROVIDER if target == "api" else inferred_api_provider
    target_provider = inferred_api_provider if target == "api" else OFFICIAL_PROVIDER
    known = paired_ids(pairs)
    workspaces = known_workspaces(pairs)
    store = ThreadStore(paths.state_db, readonly=True)
    try:
        visible = store.active_by_provider(source_provider)
        skipped_automation = 0
        skipped_test = 0
        skipped_workspace = 0
        if not include_automation:
            skipped_automation = len([record for record in visible if is_automation_thread(record)])
            visible = [record for record in visible if not is_automation_thread(record)]
        if not include_tests:
            skipped_test = len([record for record in visible if is_test_thread(record)])
            visible = [record for record in visible if not is_test_thread(record)]
        if current_workspace_only and workspaces:
            skipped_workspace = len([record for record in visible if record.cwd not in workspaces])
            visible = [record for record in visible if record.cwd in workspaces]
        paired = [record for record in visible if record.id in known]
        to_copy = [record for record in visible if record.id not in known]
        return MirrorPlan(source_provider, target_provider, visible, paired, to_copy, skipped_automation, skipped_test, skipped_workspace)
    finally:
        store.close()


def infer_mirror_providers(paths: CodexPaths, target: str, api_provider: str | None = None) -> tuple[str, str, str]:
    current_provider = read_model_provider(paths.config)
    pairs = load_pairs(paths.pairs_file)
    pair_providers = sorted({pair.api_provider for pair in pairs if pair.api_provider})
    inferred_api_provider = api_provider or (current_provider if current_provider and current_provider != OFFICIAL_PROVIDER else None)
    if not inferred_api_provider and len(pair_providers) == 1:
        inferred_api_provider = pair_providers[0]
    if not inferred_api_provider:
        raise RuntimeError("Cannot infer API provider for mirror mode.")

    source_provider = OFFICIAL_PROVIDER if target == "api" else inferred_api_provider
    target_provider = inferred_api_provider if target == "api" else OFFICIAL_PROVIDER
    return inferred_api_provider, source_provider, target_provider


def compute_mirror_diff(
    paths: CodexPaths,
    target: str,
    api_provider: str | None = None,
    include_automation: bool = False,
    current_workspace_only: bool = False,
) -> MirrorDiff:
    _, source_provider, target_provider = infer_mirror_providers(paths, target, api_provider=api_provider)
    pairs = load_pairs(paths.pairs_file)
    workspaces = known_workspaces(pairs)
    source_pair_id = (lambda pair: pair.official) if source_provider == OFFICIAL_PROVIDER else (lambda pair: pair.api)
    target_pair_id = (lambda pair: pair.api) if target_provider != OFFICIAL_PROVIDER else (lambda pair: pair.official)
    pairs_by_source_id = {source_pair_id(pair): pair for pair in pairs}
    pairs_by_target_id = {target_pair_id(pair): pair for pair in pairs}
    index_titles = load_session_index_titles(paths.session_index)

    store = ThreadStore(paths.state_db, readonly=True)
    try:
        source_all = store.by_provider(source_provider)
        target_all = store.by_provider(target_provider)
        if current_workspace_only and workspaces:
            source_all = [record for record in source_all if record.cwd in workspaces]
            target_all = [record for record in target_all if record.cwd in workspaces]
        if not include_automation:
            source_all = [record for record in source_all if not is_automation_thread(record)]
            target_all = [record for record in target_all if not is_automation_thread(record)]
        source_visible = [record for record in source_all if not record.archived]
        target_visible = [record for record in target_all if not record.archived]
        source_archived = [record for record in source_all if record.archived]
        target_archived = [record for record in target_all if record.archived]
        source_by_id = {record.id: record for record in source_all}
        target_by_id = {record.id: record for record in target_all}
        target_visible_ids = {record.id for record in target_visible}
        target_archived_ids = {record.id for record in target_archived}
        paired_source = [record for record in source_visible if record.id in pairs_by_source_id]
        missing = [
            record
            for record in source_visible
            if record.id not in pairs_by_source_id or target_pair_id(pairs_by_source_id[record.id]) not in target_visible_ids
        ]
        expected_target_ids = {target_pair_id(pairs_by_source_id[record.id]) for record in paired_source}
        extra = [record for record in target_visible if record.id not in expected_target_ids]
        source_archived_extras: list[ThreadRecord] = []
        source_active_target_archived: list[tuple[ThreadRecord, ThreadRecord]] = []
        source_archived_target_active: list[tuple[ThreadRecord, ThreadRecord]] = []
        archived_missing: list[ThreadRecord] = []
        archived_extra: list[ThreadRecord] = []
        title_mismatches: list[tuple[ThreadRecord, ThreadRecord, str, str]] = []
        order_mismatches: list[tuple[int, ThreadRecord | None, ThreadRecord | None]] = []
        timestamp_mismatches: list[tuple[ThreadRecord, ThreadRecord, int | None, int | None]] = []
        for record in extra:
            pair = pairs_by_target_id.get(record.id)
            if not pair:
                continue
            source_record = source_by_id.get(source_pair_id(pair))
            if not source_record:
                continue
            if source_record.archived:
                source_archived_extras.append(record)
        for pair in pairs:
            source_record = source_by_id.get(source_pair_id(pair))
            target_record = target_by_id.get(target_pair_id(pair))
            if not source_record or not target_record:
                continue
            if not source_record.archived and target_record.archived:
                source_active_target_archived.append((source_record, target_record))
            elif source_record.archived and not target_record.archived:
                source_archived_target_active.append((source_record, target_record))
            if not source_record.archived and not target_record.archived:
                source_title = record_display_title(source_record, index_titles)
                target_title = record_display_title(target_record, index_titles)
                if source_title != target_title:
                    title_mismatches.append((source_record, target_record, source_title, target_title))
                source_updated = source_record.data.get("updated_at")
                target_updated = target_record.data.get("updated_at")
                if source_updated != target_updated:
                    timestamp_mismatches.append((source_record, target_record, source_updated, target_updated))
        expected_order = [target_pair_id(pairs_by_source_id[record.id]) for record in paired_source]
        actual_order = [record.id for record in target_visible if record.id in expected_target_ids]
        if expected_order != actual_order:
            for index, (expected_id, actual_id) in enumerate(zip(expected_order, actual_order), 1):
                if expected_id != actual_id:
                    expected_record = target_by_id.get(expected_id)
                    actual_record = target_by_id.get(actual_id)
                    expected_updated = expected_record.data.get("updated_at") if expected_record else None
                    actual_updated = actual_record.data.get("updated_at") if actual_record else None
                    # SQLite does not define an order for equal timestamps, so either order is visually equivalent.
                    if expected_updated != actual_updated:
                        order_mismatches.append((index, expected_record, actual_record))
            if len(expected_order) > len(actual_order):
                for index, expected_id in enumerate(expected_order[len(actual_order) :], len(actual_order) + 1):
                    order_mismatches.append((index, target_by_id.get(expected_id), None))
            elif len(actual_order) > len(expected_order):
                for index, actual_id in enumerate(actual_order[len(expected_order) :], len(expected_order) + 1):
                    order_mismatches.append((index, None, target_by_id.get(actual_id)))
        for record in source_archived:
            pair = pairs_by_source_id.get(record.id)
            if not pair:
                archived_missing.append(record)
                continue
            if target_pair_id(pair) not in target_archived_ids:
                archived_missing.append(record)
        source_archived_target_ids = {
            target_pair_id(pairs_by_source_id[record.id])
            for record in source_archived
            if record.id in pairs_by_source_id
        }
        archived_extra = [record for record in target_archived if record.id not in source_archived_target_ids]
        return MirrorDiff(
            source_provider=source_provider,
            target_provider=target_provider,
            source_count=len(source_visible),
            target_count=len(target_visible),
            source_archived_count=len(source_archived),
            target_archived_count=len(target_archived),
            missing_in_target=missing,
            extra_in_target=extra,
            paired_source_archived_extras=source_archived_extras,
            source_active_target_archived=source_active_target_archived,
            source_archived_target_active=source_archived_target_active,
            archived_missing_in_target=archived_missing,
            archived_extra_in_target=archived_extra,
            title_mismatches=title_mismatches,
            order_mismatches=order_mismatches,
            timestamp_mismatches=timestamp_mismatches,
            paired_source_count=len(paired_source),
        )
    finally:
        store.close()


def report_mirror_diff(diff: MirrorDiff, prune_extras: bool = False) -> list[str]:
    messages = [
        f"镜像方向：{provider_label(diff.source_provider)} -> {provider_label(diff.target_provider)}",
        "Automation 历史运行会话默认不参与镜像。",
        f"源侧左侧会话：{diff.source_count} 条",
        f"目标侧左侧会话：{diff.target_count} 条",
        f"源侧归档会话：{diff.source_archived_count} 条",
        f"目标侧归档会话：{diff.target_archived_count} 条",
        f"已接入 handoff：{diff.paired_source_count} 条",
        f"目标侧缺少：{len(diff.missing_in_target)} 条",
        f"目标侧额外：{len(diff.extra_in_target)} 条" + ("，将归档隐藏" if prune_extras else "，默认保留"),
        f"已接入会话归档不一致：{len(diff.source_active_target_archived) + len(diff.source_archived_target_active)} 条",
        f"已接入会话标题不一致：{len(diff.title_mismatches)} 条",
        f"已接入会话排序不一致：{len(diff.order_mismatches)} 条",
        f"已接入会话更新时间不一致：{len(diff.timestamp_mismatches)} 条",
    ]
    if diff.paired_source_archived_extras:
        messages.append(f"其中源侧已归档的配对会话：{len(diff.paired_source_archived_extras)} 条，将同步归档")
    if diff.archived_missing_in_target:
        messages.append(f"源侧有 {len(diff.archived_missing_in_target)} 条归档会话未在目标侧归档列表中出现；未接入旧归档默认只提示，不自动复制。")
    if diff.archived_extra_in_target:
        messages.append(f"目标侧有 {len(diff.archived_extra_in_target)} 条额外归档会话；默认保留，不影响左侧切换。")
    for record in diff.missing_in_target[:20]:
        messages.append(f"  缺少 - {display_record(record)}")
    if len(diff.missing_in_target) > 20:
        messages.append(f"  ... 还有 {len(diff.missing_in_target) - 20} 条缺少会话")
    if prune_extras:
        for record in diff.extra_in_target[:20]:
            messages.append(f"  额外 - {display_record(record)}")
        if len(diff.extra_in_target) > 20:
            messages.append(f"  ... 还有 {len(diff.extra_in_target) - 20} 条额外会话")
    for source, target in diff.source_active_target_archived[:20]:
        messages.append(f"  归档不一致 - 源侧仍在左侧，目标侧已归档：{display_record(source)} -> {target.id}")
    for source, target in diff.source_archived_target_active[:20]:
        messages.append(f"  归档不一致 - 源侧已归档，目标侧仍在左侧：{display_record(source)} -> {target.id}")
    for source, target, source_title, target_title in diff.title_mismatches[:20]:
        messages.append(
            f"  标题不一致 - {source.id} -> {target.id}："
            f"源侧《{display_title(source_title, 40)}》，目标侧《{display_title(target_title, 40)}》"
        )
    for index, expected, actual in diff.order_mismatches[:20]:
        expected_text = display_record(expected, 50) if expected else "<缺少>"
        actual_text = display_record(actual, 50) if actual else "<缺少>"
        messages.append(f"  排序不一致 - 第 {index} 位：应为 {expected_text}，实际为 {actual_text}")
    for source, target, source_updated, target_updated in diff.timestamp_mismatches[:20]:
        messages.append(
            f"  更新时间不一致 - {source.id} -> {target.id}："
            f"源侧 {source_updated}，目标侧 {target_updated}"
        )
    return messages


def check_conclusion(diff: MirrorDiff, target: str) -> tuple[str, int]:
    target_command = f"codex-handoff {target}"
    if not diff.has_problems():
        return "结论：目标侧左侧列表一致；已接入会话的归档状态一致。", 0
    if diff.is_pending_handoff():
        return (
            "结论：这是正常的待交接状态。源侧有新的左侧会话或新内容还没带到目标侧；"
            f"切换前运行 `{target_command}` 即可。",
            1,
        )
    if diff.is_target_ahead_only():
        return (
            "结论：目标侧当前比源侧多出会话。若你刚在目标侧继续聊天，这是正常现象；"
            f"若准备切换到目标侧，请先确认方向是否正确，必要时运行 `{target_command}`。",
            1,
        )
    return (
        "结论：目标侧左侧列表或已接入会话归档状态仍不一致；"
        f"切换前应先运行 `{target_command}`。",
        1,
    )


def run_mirror(
    paths: CodexPaths,
    target: str,
    apply: bool,
    backup_base: Path,
    api_provider: str | None = None,
    include_automation: bool = False,
    include_tests: bool = False,
    current_workspace_only: bool = False,
    selected_ids: set[str] | None = None,
    prune_extras: bool = False,
    sync_paired_archive: bool = True,
    converge_visible_union: bool = True,
) -> list[str]:
    messages: list[str] = []
    diff = compute_mirror_diff(
        paths,
        target,
        api_provider=api_provider,
        include_automation=include_automation,
        current_workspace_only=current_workspace_only,
    )
    messages.extend(report_mirror_diff(diff, prune_extras=prune_extras))

    if not apply:
        return messages

    current_provider = read_model_provider(paths.config)
    pairs = load_pairs(paths.pairs_file)
    pair_providers = sorted({pair.api_provider for pair in pairs if pair.api_provider})
    inferred_api_provider = api_provider or (current_provider if current_provider and current_provider != OFFICIAL_PROVIDER else None)
    if not inferred_api_provider and len(pair_providers) == 1:
        inferred_api_provider = pair_providers[0]
    if not inferred_api_provider:
        raise RuntimeError("Cannot infer API provider for mirror mode.")

    source_provider = OFFICIAL_PROVIDER if target == "api" else inferred_api_provider
    target_provider = inferred_api_provider if target == "api" else OFFICIAL_PROVIDER
    workspaces = known_workspaces(pairs)
    source_pair_id = (lambda pair: pair.official) if source_provider == OFFICIAL_PROVIDER else (lambda pair: pair.api)
    target_pair_id = (lambda pair: pair.api) if target_provider != OFFICIAL_PROVIDER else (lambda pair: pair.official)
    pairs_by_source_id = {source_pair_id(pair): pair for pair in pairs}

    store = ThreadStore(paths.state_db, readonly=True)
    try:
        source_all = store.by_provider(source_provider)
        target_all = store.by_provider(target_provider)
        if current_workspace_only and workspaces:
            source_all = [record for record in source_all if record.cwd in workspaces]
            target_all = [record for record in target_all if record.cwd in workspaces]
        if not include_automation:
            source_all = [record for record in source_all if not is_automation_thread(record)]
            target_all = [record for record in target_all if not is_automation_thread(record)]
        source_visible = [record for record in source_all if not record.archived]
        target_visible = [record for record in target_all if not record.archived]
        paired_source = [record for record in source_visible if record.id in pairs_by_source_id]
        unpaired_source = [record for record in source_visible if record.id not in pairs_by_source_id]
        if selected_ids is not None:
            source_to_copy = [record for record in unpaired_source if record.id in selected_ids]
        else:
            source_to_copy = unpaired_source
        expected_target_ids = {target_pair_id(pairs_by_source_id[record.id]) for record in paired_source}
        target_extras = [record for record in target_visible if record.id not in expected_target_ids]
    finally:
        store.close()

    selected_to_copy = source_to_copy
    if selected_ids is not None:
        messages.append(f"本次选择接入：{len(selected_to_copy)} 条")

    backup_root = create_full_backup(paths.home, backup_base)
    messages.append("备份模式=full")
    messages.append(f"备份位置={backup_root}")
    pairs = load_pairs(paths.pairs_file)
    pairs_by_target_id = {target_pair_id(pair): pair for pair in pairs}
    source_records = {record.id: record for record in source_all}
    target_records = {record.id: record for record in target_all}
    index_titles = load_session_index_titles(paths.session_index)
    source_titles = {record.id: record_display_title(record, index_titles) for record in source_all}
    target_titles = {record.id: record_display_title(record, index_titles) for record in target_all}
    expected_target_ids = set()
    target_source_map: dict[str, ThreadRecord] = {}
    target_title_map: dict[str, str] = {}
    for pair in pairs:
        source_id = source_pair_id(pair)
        if source_id not in source_titles:
            continue
        pair.title = source_titles[source_id]
        source_record = source_records[source_id]
        target_id = target_pair_id(pair)
        target_record = target_records.get(target_id)
        title = mirror_title(source_titles[source_id], target_titles.get(target_id) if target_record else None)
        pair.title = title
        report = sync_pair(
            paths,
            pair,
            apply=True,
            forced_title=title,
            forced_archived=source_record.archived if sync_paired_archive else None,
            forced_source_id=source_record.id,
        )
        expected_target_ids.add(target_id)
        target_source_map[target_id] = source_record
        target_title_map[target_id] = title
        messages.append(report_sync_message(report))

    existing_matches, records_to_copy = match_existing_target_copies(selected_to_copy, target_extras)
    for source, target_record in existing_matches:
        pair = pair_existing_threads(source, target_record, source_provider, target_provider)
        pair.title_mode = "auto"
        pairs.append(pair)
        target_id = target_pair_id(pair)
        expected_target_ids.add(target_id)
        target_source_map[target_id] = source
        target_title_map[target_id] = record_display_title(source, index_titles)
        pairs_by_target_id[target_id] = pair
        messages.append(f"已接入已有目标侧副本：{source.id} -> official={pair.official} api={pair.api}")

    for record in records_to_copy:
        record_title = record_display_title(record, index_titles)
        pair = copy_thread_to_provider(paths, record, target_provider, apply=True, title=record_title)
        if pair:
            pair.title = record_title
            pair.title_mode = "auto"
            pairs.append(pair)
            target_id = target_pair_id(pair)
            expected_target_ids.add(target_id)
            target_source_map[target_id] = record
            target_title_map[target_id] = pair.title
            messages.append(f"已接入：{record.id} -> official={pair.official} api={pair.api}")
    if converge_visible_union and not prune_extras:
        pairs_by_known_id = paired_ids(pairs)
        reverse_records = [record for record in target_extras if record.id not in pairs_by_known_id]
        for record in reverse_records:
            record_title = record_display_title(record, index_titles)
            pair = copy_thread_to_provider(paths, record, source_provider, apply=True, title=record_title)
            if pair:
                pair.title = record_title
                pair.title_mode = "auto"
                pairs.append(pair)
                source_id = source_pair_id(pair)
                target_id = target_pair_id(pair)
                expected_target_ids.add(target_id)
                pairs_by_target_id[target_id] = pair
                messages.append(f"已反向接入目标侧新增会话：{record.id} -> official={pair.official} api={pair.api}")
                store = ThreadStore(paths.state_db)
                try:
                    store.update_from_source(source_id, record, pair.title)
                    record_updated_at = record.data.get("updated_at_ms") or record.data.get("updated_at")
                    append_session_index(paths.session_index, source_id, pair.title, record_updated_at)
                    append_session_index(paths.session_index, target_id, pair.title, record_updated_at)
                    if sync_paired_archive:
                        sync_archive_state(paths, store, source_id, record.archived)
                    store.commit()
                finally:
                    store.close()
    store = ThreadStore(paths.state_db)
    try:
        for target_id, source in target_source_map.items():
            synced_title = target_title_map.get(target_id, source.title)
            store.update_from_source(target_id, source, synced_title)
            source_updated_at = source.data.get("updated_at_ms") or source.data.get("updated_at")
            append_session_index(paths.session_index, target_id, synced_title, source_updated_at)
            append_session_index(paths.session_index, source.id, synced_title, source_updated_at)
            if sync_paired_archive:
                sync_archive_state(paths, store, target_id, source.archived)
        if prune_extras:
            for record in target_extras:
                if record.id in expected_target_ids:
                    continue
                sync_archive_state(paths, store, record.id, True)
                messages.append(f"已归档隐藏目标侧额外会话：{display_record(record)}")
        elif sync_paired_archive:
            for record in target_extras:
                pair = pairs_by_target_id.get(record.id)
                if not pair:
                    continue
                try:
                    source_record = store.get(source_pair_id(pair))
                except KeyError:
                    continue
                if source_record.archived:
                    sync_archive_state(paths, store, record.id, True)
                    messages.append(f"已同步归档源侧已归档的配对会话：{display_record(record)}")
        store.commit()
    finally:
        store.close()
    save_pairs(paths.pairs_file, pairs)
    after = compute_mirror_diff(
        paths,
        target,
        api_provider=api_provider,
        include_automation=include_automation,
        current_workspace_only=current_workspace_only,
    )
    messages.append("同步后检查：")
    messages.extend(report_mirror_diff(after, prune_extras=prune_extras))
    return messages
