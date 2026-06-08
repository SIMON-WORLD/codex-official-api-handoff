from __future__ import annotations

import json
from pathlib import Path


def load_jsonl(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8").splitlines()


def write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line if line.endswith("\n") else line + "\n" for line in lines), encoding="utf-8")


def normalize_line(line: str, source_id: str, target_id: str, target_provider: str) -> str:
    item = json.loads(line)
    if item.get("type") == "session_meta":
        payload = item.setdefault("payload", {})
        payload["id"] = target_id
        payload["model_provider"] = target_provider
    payload = item.get("payload")
    if isinstance(payload, dict):
        payload.pop("encrypted_content", None)
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rewrite_extra_line(line: str, source_id: str, target_id: str, target_provider: str) -> str | None:
    item = json.loads(line)
    if item.get("type") == "session_meta":
        return None

    payload = item.get("payload")
    if isinstance(payload, dict):
        payload.pop("encrypted_content", None)
        if payload.get("id") == source_id:
            payload["id"] = target_id
        if payload.get("thread_id") == source_id:
            payload["thread_id"] = target_id
        if payload.get("model_provider"):
            payload["model_provider"] = target_provider

    return json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"


def common_prefix(source_lines: list[str], target_lines: list[str], source_id: str, target_id: str, target_provider: str) -> int:
    common = 0
    for target_line, source_line in zip(target_lines, source_lines):
        if normalize_line(target_line, target_id, target_id, target_provider) != normalize_line(
            source_line, source_id, target_id, target_provider
        ):
            break
        common += 1
    return common


def encrypted_count(lines: list[str]) -> int:
    count = 0
    for line in lines:
        payload = json.loads(line).get("payload")
        if isinstance(payload, dict) and "encrypted_content" in payload:
            count += 1
    return count

