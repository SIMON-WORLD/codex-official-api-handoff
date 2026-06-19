import json
import tempfile
import unittest
from pathlib import Path

from codex_official_api_handoff.handoff import (
    MirrorDiff,
    check_conclusion,
    is_automation_thread,
    load_session_index_titles,
    mirror_title,
    pinned_pair_diff,
    record_display_title,
    relocate_rollout_file,
    preferred_title,
    session_index_timestamp,
    sync_pinned_state,
    sync_pair_metadata,
)
from codex_official_api_handoff.pairs import Pair
from codex_official_api_handoff.paths import CodexPaths
from codex_official_api_handoff.rollout import common_prefix, rewrite_rollout_for_target
from codex_official_api_handoff.short_cli import parse_selection
from codex_official_api_handoff.sqlite_store import ThreadRecord


def line(item):
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


class SyncLogicTests(unittest.TestCase):
    def test_linear_source_has_target_as_prefix(self):
        target = [
            line({"type": "session_meta", "payload": {"id": "official", "model_provider": "openai"}}),
            line({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "A"}]}}),
        ]
        source = [
            line({"type": "session_meta", "payload": {"id": "api", "model_provider": "custom"}}),
            line({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "A"}]}}),
            line({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "B"}]}}),
        ]
        self.assertEqual(common_prefix(source, target, "api", "official", "openai"), 2)

    def test_conflict_stops_prefix_match(self):
        target = [
            line({"type": "session_meta", "payload": {"id": "official", "model_provider": "openai"}}),
            line({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "X"}]}}),
        ]
        source = [
            line({"type": "session_meta", "payload": {"id": "api", "model_provider": "custom"}}),
            line({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "Y"}]}}),
        ]
        self.assertEqual(common_prefix(source, target, "api", "official", "openai"), 1)

    def test_complete_rollout_rewrite_keeps_target_identity_and_removes_encryption(self):
        source = [
            line({"type": "session_meta", "payload": {"id": "official", "model_provider": "openai"}}),
            line(
                {
                    "type": "response_item",
                    "payload": {
                        "thread_id": "official",
                        "model_provider": "openai",
                        "encrypted_content": "provider-bound",
                        "content": "kept",
                    },
                }
            ),
        ]

        rewritten = [json.loads(item) for item in rewrite_rollout_for_target(source, "official", "api", "custom")]

        self.assertEqual(rewritten[0]["payload"]["id"], "api")
        self.assertEqual(rewritten[0]["payload"]["model_provider"], "custom")
        self.assertEqual(rewritten[1]["payload"]["thread_id"], "api")
        self.assertEqual(rewritten[1]["payload"]["model_provider"], "custom")
        self.assertNotIn("encrypted_content", rewritten[1]["payload"])
        self.assertEqual(rewritten[1]["payload"]["content"], "kept")

    def test_auto_title_adopts_one_sided_change(self):
        pair = Pair("main", "official", "api", "custom", title="old")
        official = ThreadRecord({"id": "official", "model_provider": "openai", "title": "old", "rollout_path": "x", "updated_at": 1})
        api = ThreadRecord({"id": "api", "model_provider": "custom", "title": "new", "rollout_path": "y", "updated_at": 2})

        self.assertEqual(preferred_title(pair, official, api), "new")
        self.assertEqual(pair.title, "new")

    def test_auto_title_rejects_two_sided_conflict(self):
        pair = Pair("main", "official", "api", "custom", title="old")
        official = ThreadRecord({"id": "official", "model_provider": "openai", "title": "official title", "rollout_path": "x", "updated_at": 3})
        api = ThreadRecord({"id": "api", "model_provider": "custom", "title": "api title", "rollout_path": "y", "updated_at": 4})

        with self.assertRaises(RuntimeError):
            preferred_title(pair, official, api)

    def test_mirror_title_does_not_overwrite_manual_title_with_generic_title(self):
        self.assertEqual(mirror_title("你好", "codex 官方<-> API 会话交接 开发"), "codex 官方<-> API 会话交接 开发")
        self.assertEqual(mirror_title("新的人工标题", "codex 官方<-> API 会话交接 开发"), "新的人工标题")
        self.assertEqual(mirror_title("你好，我之前在这个文件夹下跟你有很多对话", "01 主线"), "01 主线")

    def test_session_index_title_overrides_sqlite_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_index.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "thread", "thread_name": "旧标题"}, ensure_ascii=False),
                        json.dumps({"id": "thread", "thread_name": "01 主线"}, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            record = ThreadRecord(
                {"id": "thread", "model_provider": "openai", "title": "你好，我之前在这个文件夹下跟你有很多对话", "rollout_path": "x"}
            )

            titles = load_session_index_titles(path)

            self.assertEqual(record_display_title(record, titles), "01 主线")

    def test_session_index_timestamp_preserves_unix_seconds(self):
        self.assertEqual(session_index_timestamp(0), "1970-01-01T00:00:00.000Z")

    def test_session_index_timestamp_preserves_unix_milliseconds(self):
        self.assertEqual(session_index_timestamp(1_000), "1970-01-01T00:16:40.000Z")
        self.assertEqual(session_index_timestamp(1_700_000_000_123), "2023-11-14T22:13:20.123Z")

    def test_automation_detection_uses_title_or_first_message(self):
        title_record = ThreadRecord(
            {"id": "a", "model_provider": "custom", "title": "Automation: nightly", "rollout_path": "x"}
        )
        message_record = ThreadRecord(
            {
                "id": "b",
                "model_provider": "custom",
                "title": "Ready 稿统一 raw 巡检",
                "first_user_message": "Automation: Ready 稿统一 raw 巡检\nAutomation ID: test",
                "rollout_path": "y",
            }
        )
        normal_record = ThreadRecord(
            {"id": "c", "model_provider": "custom", "title": "讨论 Automation 设计", "rollout_path": "z"}
        )

        self.assertTrue(is_automation_thread(title_record))
        self.assertTrue(is_automation_thread(message_record))
        self.assertFalse(is_automation_thread(normal_record))

    def test_relocate_rollout_file_moves_archived_thread_out_of_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = home / "sessions" / "2026" / "06" / "12" / "rollout-2026-06-12T00-00-00-thread.jsonl"
            source.parent.mkdir(parents=True)
            source.write_text("x", encoding="utf-8")
            record = ThreadRecord({"id": "thread", "model_provider": "custom", "title": "t", "rollout_path": str(source)})

            moved = relocate_rollout_file(CodexPaths(home), record, archived=True)

            self.assertEqual(moved, home / "archived_sessions" / source.name)
            self.assertTrue(moved.exists())
            self.assertFalse(source.exists())

    def test_archive_sync_prefers_archived_even_when_older(self):
        class Store:
            def __init__(self):
                self.archived = {}
                self.titles = {}

            def update_title(self, thread_id, title):
                self.titles[thread_id] = title

            def update_archived(self, thread_id, archived):
                self.archived[thread_id] = archived

        store = Store()
        pair = Pair("main", "official", "api", "custom", title="same")
        official = ThreadRecord(
            {"id": "official", "model_provider": "openai", "title": "same", "rollout_path": "x", "archived": 0, "updated_at": 20}
        )
        api = ThreadRecord(
            {"id": "api", "model_provider": "custom", "title": "same", "rollout_path": "y", "archived": 1, "updated_at": 10}
        )

        sync_pair_metadata(store, pair, official, api, "same")

        self.assertEqual(store.archived, {"official": True, "api": True})

    def test_selection_parser_supports_ranges_and_all(self):
        self.assertEqual(parse_selection("1,3-4", 5), [1, 3, 4])
        self.assertEqual(parse_selection("all", 3), [1, 2, 3])

    def test_mirror_diff_flags_paired_archive_mismatch(self):
        source = ThreadRecord({"id": "official", "model_provider": "openai", "title": "same", "rollout_path": "x", "archived": 1})
        target = ThreadRecord({"id": "api", "model_provider": "custom", "title": "same", "rollout_path": "y", "archived": 0})
        diff = MirrorDiff(
            source_provider="openai",
            target_provider="custom",
            source_count=0,
            target_count=1,
            source_archived_count=1,
            target_archived_count=0,
            missing_in_target=[],
            extra_in_target=[],
            paired_source_archived_extras=[target],
            source_active_target_archived=[],
            source_archived_target_active=[(source, target)],
            archived_missing_in_target=[source],
            archived_extra_in_target=[],
            title_mismatches=[],
            order_mismatches=[],
            timestamp_mismatches=[],
            pinned_missing_in_target=[],
            pinned_extra_in_target=[],
            paired_source_count=0,
        )

        self.assertTrue(diff.has_problems())

    def test_mirror_diff_ignores_legacy_unpaired_archived_extras(self):
        legacy = ThreadRecord({"id": "legacy", "model_provider": "openai", "title": "old", "rollout_path": "x", "archived": 1})
        diff = MirrorDiff(
            source_provider="custom",
            target_provider="openai",
            source_count=2,
            target_count=2,
            source_archived_count=0,
            target_archived_count=1,
            missing_in_target=[],
            extra_in_target=[],
            paired_source_archived_extras=[],
            source_active_target_archived=[],
            source_archived_target_active=[],
            archived_missing_in_target=[],
            archived_extra_in_target=[legacy],
            title_mismatches=[],
            order_mismatches=[],
            timestamp_mismatches=[],
            pinned_missing_in_target=[],
            pinned_extra_in_target=[],
            paired_source_count=2,
        )

        self.assertFalse(diff.has_problems())

    def test_check_conclusion_names_pending_handoff(self):
        missing = ThreadRecord({"id": "new", "model_provider": "custom", "title": "new work", "rollout_path": "x"})
        diff = MirrorDiff(
            source_provider="custom",
            target_provider="openai",
            source_count=1,
            target_count=0,
            source_archived_count=0,
            target_archived_count=0,
            missing_in_target=[missing],
            extra_in_target=[],
            paired_source_archived_extras=[],
            source_active_target_archived=[],
            source_archived_target_active=[],
            archived_missing_in_target=[],
            archived_extra_in_target=[],
            title_mismatches=[],
            order_mismatches=[],
            timestamp_mismatches=[],
            pinned_missing_in_target=[],
            pinned_extra_in_target=[],
            paired_source_count=0,
        )

        message, code = check_conclusion(diff, "official")

        self.assertEqual(code, 1)
        self.assertIn("正常的待交接状态", message)
        self.assertIn("codex-handoff official", message)

    def test_check_conclusion_names_target_ahead(self):
        extra = ThreadRecord({"id": "extra", "model_provider": "custom", "title": "extra", "rollout_path": "x"})
        diff = MirrorDiff(
            source_provider="openai",
            target_provider="custom",
            source_count=0,
            target_count=1,
            source_archived_count=0,
            target_archived_count=0,
            missing_in_target=[],
            extra_in_target=[extra],
            paired_source_archived_extras=[],
            source_active_target_archived=[],
            source_archived_target_active=[],
            archived_missing_in_target=[],
            archived_extra_in_target=[],
            title_mismatches=[],
            order_mismatches=[],
            timestamp_mismatches=[],
            pinned_missing_in_target=[],
            pinned_extra_in_target=[],
            paired_source_count=0,
        )

        message, code = check_conclusion(diff, "api")

        self.assertEqual(code, 1)
        self.assertIn("目标侧当前比源侧多出会话", message)

    def test_mirror_diff_flags_title_mismatch(self):
        source = ThreadRecord({"id": "official", "model_provider": "openai", "title": "01 主线", "rollout_path": "x", "archived": 0})
        target = ThreadRecord({"id": "api", "model_provider": "custom", "title": "旧标题", "rollout_path": "y", "archived": 0})
        diff = MirrorDiff(
            source_provider="openai",
            target_provider="custom",
            source_count=1,
            target_count=1,
            source_archived_count=0,
            target_archived_count=0,
            missing_in_target=[],
            extra_in_target=[],
            paired_source_archived_extras=[],
            source_active_target_archived=[],
            source_archived_target_active=[],
            archived_missing_in_target=[],
            archived_extra_in_target=[],
            title_mismatches=[(source, target, "01 主线", "旧标题")],
            order_mismatches=[],
            timestamp_mismatches=[],
            pinned_missing_in_target=[],
            pinned_extra_in_target=[],
            paired_source_count=1,
        )

        self.assertTrue(diff.has_problems())

    def test_mirror_diff_flags_order_mismatch(self):
        first = ThreadRecord(
            {"id": "first", "model_provider": "custom", "title": "first", "rollout_path": "x", "archived": 0, "updated_at": 2}
        )
        second = ThreadRecord(
            {"id": "second", "model_provider": "custom", "title": "second", "rollout_path": "y", "archived": 0, "updated_at": 1}
        )
        diff = MirrorDiff(
            source_provider="openai",
            target_provider="custom",
            source_count=2,
            target_count=2,
            source_archived_count=0,
            target_archived_count=0,
            missing_in_target=[],
            extra_in_target=[],
            paired_source_archived_extras=[],
            source_active_target_archived=[],
            source_archived_target_active=[],
            archived_missing_in_target=[],
            archived_extra_in_target=[],
            title_mismatches=[],
            order_mismatches=[(1, first, second)],
            timestamp_mismatches=[],
            pinned_missing_in_target=[],
            pinned_extra_in_target=[],
            paired_source_count=2,
        )

        self.assertTrue(diff.has_problems())

    def test_mirror_diff_flags_timestamp_mismatch(self):
        source = ThreadRecord({"id": "official", "model_provider": "openai", "title": "same", "rollout_path": "x", "archived": 0})
        target = ThreadRecord({"id": "api", "model_provider": "custom", "title": "same", "rollout_path": "y", "archived": 0})
        diff = MirrorDiff(
            source_provider="openai",
            target_provider="custom",
            source_count=1,
            target_count=1,
            source_archived_count=0,
            target_archived_count=0,
            missing_in_target=[],
            extra_in_target=[],
            paired_source_archived_extras=[],
            source_active_target_archived=[],
            source_archived_target_active=[],
            archived_missing_in_target=[],
            archived_extra_in_target=[],
            title_mismatches=[],
            order_mismatches=[],
            timestamp_mismatches=[(source, target, 1, 2)],
            pinned_missing_in_target=[],
            pinned_extra_in_target=[],
            paired_source_count=1,
        )

        self.assertTrue(diff.has_problems())

    def test_mirror_diff_flags_pinned_mismatch(self):
        diff = MirrorDiff(
            source_provider="openai",
            target_provider="custom",
            source_count=1,
            target_count=1,
            source_archived_count=0,
            target_archived_count=0,
            missing_in_target=[],
            extra_in_target=[],
            paired_source_archived_extras=[],
            source_active_target_archived=[],
            source_archived_target_active=[],
            archived_missing_in_target=[],
            archived_extra_in_target=[],
            title_mismatches=[],
            order_mismatches=[],
            timestamp_mismatches=[],
            pinned_missing_in_target=[("official", "api")],
            pinned_extra_in_target=[],
            paired_source_count=1,
        )

        self.assertTrue(diff.has_problems())

    def test_sync_pinned_state_maps_source_pins_to_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = CodexPaths(home)
            home.mkdir(parents=True, exist_ok=True)
            paths.global_state.write_text(
                json.dumps(
                    {
                        "electron-persisted-atom-state": {
                            "pinned-thread-ids": ["official", "stale-api", "unrelated"]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            pairs = [
                Pair("main", "official", "api", "custom"),
                Pair("stale", "stale-official", "stale-api", "custom"),
            ]

            report = sync_pinned_state(paths, pairs, lambda pair: pair.official, lambda pair: pair.api, apply=True)
            diff = pinned_pair_diff(paths, pairs, lambda pair: pair.official, lambda pair: pair.api)
            saved = json.loads(paths.global_state.read_text(encoding="utf-8"))
            pinned = saved["electron-persisted-atom-state"]["pinned-thread-ids"]

            self.assertTrue(report.changed)
            self.assertIn("api", pinned)
            self.assertIn("official", pinned)
            self.assertIn("unrelated", pinned)
            self.assertNotIn("stale-api", pinned)
            self.assertEqual(diff.missing_in_target, [])
            self.assertEqual(diff.extra_in_target, [])


if __name__ == "__main__":
    unittest.main()
