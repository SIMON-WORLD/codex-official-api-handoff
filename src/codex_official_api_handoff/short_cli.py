from __future__ import annotations

import argparse
from pathlib import Path

from .handoff import run_to
from .paths import CodexPaths, default_codex_home


def ask_yes_no(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes", "是", "好"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-handoff")
    parser.add_argument("target", choices=["api", "official"], help="要切换到 API 还是官方账号。")
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
    parser.add_argument("--backup-base", type=Path, default=Path(r"D:\codex-backups\codex-official-api-handoff"))
    parser.add_argument("--backup", choices=["quick", "full"], default="quick")
    parser.add_argument("--yes", action="store_true", help="跳过确认，直接执行。")
    args = parser.parse_args(argv)

    paths = CodexPaths(args.codex_home)
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
