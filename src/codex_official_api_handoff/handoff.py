from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .backup import create_full_backup, create_quick_backup
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
    title: str | None = None


def provider_label(provider: str) -> str:
    return "官方" if provider == OFFICIAL_PROVIDER else "API"


def direction_label(direction: str) -> str:
    labels = {
        "none": "无需同步",
        "api-to-official": "API -> 官方",
        "official-to-api": "官方 -> API",
    }
    return labels.get(direction, direction)


def report_sync_message(report: SyncReport) -> str:
    if report.direction == "none":
        title = f"，标题：{report.title}" if report.title else ""
        return f"会话 {report.pair.name}：两边已经一致{title}。"
    source = "API" if report.direction == "api-to-official" else "官方"
    target = "官方" if report.direction == "api-to-official" else "API"
    encrypted = f"，已忽略 provider 加密片段 {report.encrypted_removed} 个" if report.encrypted_removed else ""
    title = f"，标题：{report.title}" if report.title else ""
    return f"会话 {report.pair.name}：发现{source}侧有新增内容记录 {report.extra_lines} 条，准备同步到{target}{encrypted}{title}。"


def append_session_index(path: Path, thread_id: str, title: str) -> None:
    entry = {
        "id": thread_id,
        "thread_name": title,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def preferred_title(pair: Pair, official: ThreadRecord, api: ThreadRecord) -> str:
    if pair.title:
        return pair.title
    official_updated = official.data.get("updated_at") or 0
    api_updated = api.data.get("updated_at") or 0
    if api_updated >= official_updated and api.title:
        return api.title
    if official.title:
        return official.title
    return api.title or official.title


def sync_pair(paths: CodexPaths, pair: Pair, apply: bool) -> SyncReport:
    store = ThreadStore(paths.state_db, readonly=not apply)
    try:
        official = store.get(pair.official)
        api = store.get(pair.api)
        title = preferred_title(pair, official, api)
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
            if apply and title:
                store.update_title(official.id, title)
                store.update_title(api.id, title)
                store.commit()
                append_session_index(paths.session_index, official.id, title)
                append_session_index(paths.session_index, api.id, title)
            return SyncReport(pair, "none", 0, 0, apply, title=title)
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
            if title:
                store.update_title(official.id, title)
                store.update_title(api.id, title)
            store.commit()
            append_session_index(paths.session_index, target.id, title or target.title)
            append_session_index(paths.session_index, source.id, title or source.title)

        return SyncReport(pair, direction, len(extra), encrypted_count(extra), apply, title=title)
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
    append_session_index(paths.session_index, pair.official, title)
    append_session_index(paths.session_index, pair.api, title)
    messages.append("标题已更新。")
    return messages


def mirror_plan(
    paths: CodexPaths,
    target: str,
    api_provider: str | None = None,
    include_automation: bool = False,
) -> tuple[str, str, list[ThreadRecord], list[ThreadRecord], list[ThreadRecord]]:
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
    store = ThreadStore(paths.state_db, readonly=True)
    try:
        visible = store.active_by_provider(source_provider)
        automation = [record for record in visible if is_automation_thread(record)]
        if not include_automation:
            visible = [record for record in visible if not is_automation_thread(record)]
        paired = [record for record in visible if record.id in known]
        to_copy = [record for record in visible if record.id not in known]
        return source_provider, target_provider, visible, paired, to_copy
    finally:
        store.close()


def run_mirror(
    paths: CodexPaths,
    target: str,
    apply: bool,
    backup_base: Path,
    api_provider: str | None = None,
    include_automation: bool = False,
) -> list[str]:
    messages: list[str] = []
    source_provider, target_provider, visible, paired, to_copy = mirror_plan(
        paths, target, api_provider=api_provider, include_automation=include_automation
    )
    messages.append(f"镜像方向：{provider_label(source_provider)} -> {provider_label(target_provider)}")
    messages.append(f"源侧可见会话：{len(visible)} 条")
    messages.append(f"已接入 handoff：{len(paired)} 条")
    messages.append(f"将新增接入：{len(to_copy)} 条")
    for record in to_copy[:20]:
        messages.append(f"  - {record.id}  {record.title.replace(chr(10), ' ')[:80]}")
    if len(to_copy) > 20:
        messages.append(f"  ... 还有 {len(to_copy) - 20} 条")

    if not apply:
        messages.append("dry_run=true")
        return messages

    backup_root = create_full_backup(paths.home, backup_base)
    messages.append("备份模式=full")
    messages.append(f"备份位置={backup_root}")
    pairs = load_pairs(paths.pairs_file)
    for record in to_copy:
        pair = copy_thread_to_provider(paths, record, target_provider, apply=True)
        if pair:
            pair.title = record.title
            pairs.append(pair)
            messages.append(f"已接入：{record.id} -> official={pair.official} api={pair.api}")
    save_pairs(paths.pairs_file, pairs)
    return messages
