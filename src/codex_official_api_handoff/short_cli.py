from __future__ import annotations

import argparse
from pathlib import Path

from .config import read_model_provider
from .handoff import OFFICIAL_PROVIDER, copy_thread_to_provider, is_automation_thread, mirror_plan, run_mirror, run_to
from .pairs import load_pairs, paired_ids, save_pairs
from .paths import CodexPaths, default_codex_home
from .sqlite_store import ThreadRecord, ThreadStore


def ask_yes_no(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes", "是", "好"}


def parse_selection(text: str, count: int) -> list[int]:
    if text.strip().lower() in {"all", "全部"}:
        return list(range(1, count + 1))
    selected: list[int] = []
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            selected.extend(range(start, end + 1))
        else:
            selected.append(int(part))
    unique = []
    for item in selected:
        if item < 1 or item > count:
            raise ValueError(f"编号超出范围: {item}")
        if item not in unique:
            unique.append(item)
    return unique


def infer_api_provider(paths: CodexPaths) -> str:
    current_provider = read_model_provider(paths.config)
    if current_provider and current_provider != OFFICIAL_PROVIDER:
        return current_provider
    pairs = load_pairs(paths.pairs_file)
    providers = sorted({pair.api_provider for pair in pairs if pair.api_provider})
    if len(providers) == 1:
        return providers[0]
    raise RuntimeError("无法自动判断 API provider。请先在完整命令中指定 --api-provider。")


def candidate_threads(paths: CodexPaths, target: str) -> tuple[str, str, list[ThreadRecord]]:
    api_provider = infer_api_provider(paths)
    source_provider = OFFICIAL_PROVIDER if target == "api" else api_provider
    target_provider = api_provider if target == "api" else OFFICIAL_PROVIDER
    known = paired_ids(load_pairs(paths.pairs_file))
    store = ThreadStore(paths.state_db, readonly=True)
    try:
        candidates = [
            record
            for record in store.active_by_provider(source_provider)
            if record.id not in known and not is_automation_thread(record)
        ]
        return source_provider, target_provider, candidates
    finally:
        store.close()


def run_connect(paths: CodexPaths, target: str, backup_base: Path, yes: bool = False) -> int:
    source_provider, target_provider, candidates = candidate_threads(paths, target)
    source_label = "官方" if source_provider == OFFICIAL_PROVIDER else "API"
    target_label = "API" if target_provider != OFFICIAL_PROVIDER else "官方"
    print(f"准备接入会话：{source_label} -> {target_label}")
    if not candidates:
        print("没有发现未接入会话。")
        return 0

    print(f"检测到 {len(candidates)} 条未接入会话，显示前 20 条：")
    shown = candidates[:20]
    for index, record in enumerate(shown, 1):
        title = record.title.replace("\n", " ")[:80]
        print(f"[{index}] {record.id}  {title}")
    selection = input("选择要接入的编号，例如 1,3,5；直接回车跳过：").strip()
    if not selection:
        print("已跳过。")
        return 0
    indexes = parse_selection(selection, len(shown))
    selected = [shown[index - 1] for index in indexes]
    print(f"将接入 {len(selected)} 条会话。")
    if len(selected) > 5:
        print("提示：一次接入超过 5 条，建议先确认你确实需要这些会话。")
    if not yes and not ask_yes_no("确认备份并复制这些会话吗？"):
        print("已取消。")
        return 0

    from .backup import create_quick_backup

    backup_files = [paths.state_db, paths.session_index, paths.pairs_file] + [record.rollout_path for record in selected]
    backup_root = create_quick_backup(paths.home, backup_base, backup_files)
    print(f"备份位置={backup_root}")

    pairs = load_pairs(paths.pairs_file)
    for record in selected:
        pair = copy_thread_to_provider(paths, record, target_provider, apply=True)
        if pair:
            pairs.append(pair)
            print(f"已接入：{record.id} -> official={pair.official} api={pair.api}")
    save_pairs(paths.pairs_file, pairs)
    print("完成。")
    return 0


def ask_candidate_selection(candidates: list[ThreadRecord]) -> set[str]:
    if not candidates:
        return set()
    shown = candidates[:50]
    print()
    print(f"发现 {len(candidates)} 条尚未接入的会话，默认不会全部复制。")
    print("请选择要带到另一侧的会话；如果只是同步已经接入的会话，直接回车即可。")
    for index, record in enumerate(shown, 1):
        title = record.title.replace("\n", " ")[:80]
        print(f"[{index}] {record.id}  {title}")
    if len(candidates) > len(shown):
        print(f"... 还有 {len(candidates) - len(shown)} 条未显示。")
    selection = input("输入编号，例如 1,3,5 或 1-5；输入 all 表示全部；直接回车表示不接入新会话：").strip()
    if not selection:
        return set()
    indexes = parse_selection(selection, len(shown))
    return {shown[index - 1].id for index in indexes}


def candidate_ids(candidates: list[ThreadRecord]) -> set[str]:
    return {record.id for record in candidates}


def run_mirror_short(paths: CodexPaths, target: str, backup_base: Path, yes: bool = False) -> int:
    target_label = "API" if target == "api" else "官方账号"
    print(f"准备镜像左侧会话列表到：{target_label}")
    print("先进行预览，不会写入文件。")
    print()

    dry_messages = run_mirror(paths, target, apply=False, backup_base=backup_base)
    for message in dry_messages:
        print(message)

    print()
    print("确认后会补齐目标侧缺少的会话，并同步已接入会话的内容、标题和排序。")
    print("默认不会归档或隐藏目标侧额外会话。")
    if not yes and not ask_yes_no("确认执行镜像并使用 full 完整备份吗？"):
        print("已取消。")
        return 0

    print("开始执行...")
    apply_messages = run_mirror(
        paths,
        target,
        apply=True,
        backup_base=backup_base,
    )
    for message in apply_messages:
        print(message)
    print(f"完成。现在可以用 cc-switch 切换到：{target_label}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-handoff")
    parser.add_argument("target", choices=["api", "official", "connect", "mirror"], help="要切换到 API、官方账号，接入更多会话，或镜像左侧列表。")
    parser.add_argument("connect_target", nargs="?", choices=["api", "official"], help="connect 时要接入到哪里。")
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
    parser.add_argument("--backup-base", type=Path, default=Path(r"D:\codex-backups\codex-official-api-handoff"))
    parser.add_argument("--backup", choices=["quick", "full"], default="quick")
    parser.add_argument("--yes", action="store_true", help="跳过确认，直接执行。")
    args = parser.parse_args(argv)

    paths = CodexPaths(args.codex_home)
    if args.target == "connect":
        if not args.connect_target:
            parser.error("connect 需要指定 api 或 official")
        return run_connect(paths, args.connect_target, args.backup_base, yes=args.yes)

    if args.target == "mirror":
        if not args.connect_target:
            parser.error("mirror 需要指定 api 或 official")
        return run_mirror_short(paths, args.connect_target, args.backup_base, yes=args.yes)

    target_label = "API" if args.target == "api" else "官方账号"
    print(f"准备交接到：{target_label}")
    print("先进行预览，不会写入文件。")
    print()

    dry_messages = run_to(paths, args.target, apply=False, api_provider=None, backup_base=args.backup_base)
    for message in dry_messages:
        print(message)

    if any("Conflict" in message for message in dry_messages):
        print("检测到冲突，已停止。")
        return 1

    print()
    if not args.yes and not ask_yes_no(f"确认执行同步并使用 {args.backup} 备份吗？"):
        print("已取消。")
        return 0

    print("开始执行...")
    apply_messages = run_to(
        paths,
        args.target,
        apply=True,
        api_provider=None,
        backup_base=args.backup_base,
        backup_mode=args.backup,
    )
    for message in apply_messages:
        print(message)
    print(f"完成。现在可以用 cc-switch 切换到：{target_label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
