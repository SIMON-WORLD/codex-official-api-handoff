import json
import unittest

from codex_official_api_handoff.rollout import common_prefix


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


if __name__ == "__main__":
    unittest.main()

