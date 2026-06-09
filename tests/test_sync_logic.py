import json
import unittest

from codex_official_api_handoff.handoff import preferred_title, sync_pair_metadata
from codex_official_api_handoff.pairs import Pair
from codex_official_api_handoff.rollout import common_prefix
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


if __name__ == "__main__":
    unittest.main()
