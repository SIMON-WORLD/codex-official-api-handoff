from __future__ import annotations

import argparse
from pathlib import Path

from .config import read_model_provider
from .handoff import copy_one, refresh_session_index, run_to, set_pair_title
from .pairs import Pair, load_pairs, pair_names, save_pairs
from .paths import CodexPaths, default_codex_home
from .sqlite_store import ThreadStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-official-api-handoff")
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
    parser.add_argument(
        "--backup-base",
        type=Path,
        default=Path(r"D:\codex-backups\codex-official-api-handoff"),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor")

    to_parser = subparsers.add_parser("to")
    to_parser.add_argument("target", choices=["api", "official"])
    to_parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    to_parser.add_argument("--api-provider", help="API provider id, e.g. openai-chat-completions.")
    to_parser.add_argument("--copy-new", action="store_true", help="Copy unpaired active source-provider threads.")
    to_parser.add_argument("--include-automation", action="store_true", help="Allow copying Automation: threads.")
    to_parser.add_argument("--show-new", action="store_true", help="List unpaired copy-new candidates in dry-run output.")
    to_parser.add_argument("--backup", choices=["quick", "full"], default="full", help="Backup mode for --apply.")

    pair_parser = subparsers.add_parser("pair")
    pair_subparsers = pair_parser.add_subparsers(dest="pair_command", required=True)
    pair_subparsers.add_parser("list")
    add_parser = pair_subparsers.add_parser("add")
    add_parser.add_argument("name")
    add_parser.add_argument("--official", required=True)
    add_parser.add_argument("--api", required=True)
    add_parser.add_argument("--api-provider", required=True)
    add_parser.add_argument("--workspace")

    copy_parser = subparsers.add_parser("copy-one")
    copy_parser.add_argument("thread_id")
    copy_parser.add_argument("--to", choices=["api", "official"], required=True)
    copy_parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    copy_parser.add_argument("--api-provider", help="API provider id, e.g. openai-chat-completions.")
    copy_parser.add_argument("--name", help="Pair name to store when --apply is used.")

    title_parser = subparsers.add_parser("title")
    title_parser.add_argument("pair_name")
    title_parser.add_argument("title")
    title_parser.add_argument("--apply", action="store_true")

    index_parser = subparsers.add_parser("refresh-index")
    index_parser.add_argument("--apply", action="store_true")

    return parser


def run_doctor(paths: CodexPaths) -> int:
    print(f"codex_home={paths.home}")
    print(f"config_exists={paths.config.exists()}")
    print(f"auth_exists={paths.auth.exists()}")
    print(f"model_provider={read_model_provider(paths.config)}")
    print(f"pairs_file={paths.pairs_file}")
    print(f"pairs={len(load_pairs(paths.pairs_file))}")

    store = ThreadStore(paths.state_db, readonly=True)
    try:
        for provider, count in sorted(store.provider_counts().items()):
            print(f"threads[{provider}]={count}")
    finally:
        store.close()
    return 0


def run_pair(paths: CodexPaths, args: argparse.Namespace) -> int:
    pairs = load_pairs(paths.pairs_file)
    if args.pair_command == "list":
        if not pairs:
            print("pairs=0")
            return 0
        for pair in pairs:
            print(f"{pair.name}: official={pair.official} api={pair.api} api_provider={pair.api_provider}")
        return 0

    if args.pair_command == "add":
        if args.name in pair_names(pairs):
            raise SystemExit(f"Pair already exists: {args.name}")
        pairs.append(
            Pair(
                name=args.name,
                official=args.official,
                api=args.api,
                api_provider=args.api_provider,
                workspace=args.workspace,
            )
        )
        save_pairs(paths.pairs_file, pairs)
        print(f"added pair {args.name}")
        return 0

    raise SystemExit("Unknown pair command")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = CodexPaths(args.codex_home)

    if args.command == "doctor":
        return run_doctor(paths)

    if args.command == "pair":
        return run_pair(paths, args)

    if args.command == "copy-one":
        messages = copy_one(
            paths,
            source_id=args.thread_id,
            target=args.to,
            apply=args.apply,
            api_provider=args.api_provider,
            backup_base=args.backup_base,
            name=args.name,
        )
        for message in messages:
            print(message)
        return 0

    if args.command == "title":
        for message in set_pair_title(paths, args.pair_name, args.title, apply=args.apply):
            print(message)
        return 0

    if args.command == "refresh-index":
        for message in refresh_session_index(paths, apply=args.apply, backup_base=args.backup_base):
            print(message)
        if not args.apply:
            print("rerun with --apply to write changes")
        return 0

    if args.command == "to":
        messages = run_to(
            paths,
            args.target,
            apply=args.apply,
            api_provider=args.api_provider,
            backup_base=args.backup_base,
            copy_new=args.copy_new,
            include_automation=args.include_automation,
            show_new=args.show_new,
            backup_mode=args.backup,
        )
        for message in messages:
            print(message)
        if not args.apply:
            print("dry_run=true")
            print("rerun with --apply to write changes")
        return 0

    parser.error("Unknown command")
    return 2
