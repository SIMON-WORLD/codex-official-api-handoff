import json
import unittest

from codex_official_api_handoff.rollout import common_prefix, encrypted_count, rewrite_extra_line


def line(item):
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


class RolloutTests(unittest.TestCase):
    def test_common_prefix_ignores_encrypted_content(self):
        source = [
            line({"type": "session_meta", "payload": {"id": "api", "model_provider": "api-provider"}}),
            line({"type": "response_item", "payload": {"type": "reasoning", "summary": [], "encrypted_content": "abc"}}),
        ]
        target = [
            line({"type": "session_meta", "payload": {"id": "official", "model_provider": "openai"}}),
            line({"type": "response_item", "payload": {"type": "reasoning", "summary": []}}),
        ]
        self.assertEqual(common_prefix(source, target, "api", "official", "openai"), 2)

    def test_rewrite_extra_line_removes_encrypted_content(self):
        source = line(
            {
                "type": "response_item",
                "payload": {"type": "reasoning", "summary": [], "encrypted_content": "abc", "thread_id": "api"},
            }
        )
        rewritten = rewrite_extra_line(source, "api", "official", "openai")
        payload = json.loads(rewritten)["payload"]
        self.assertNotIn("encrypted_content", payload)
        self.assertEqual(payload["thread_id"], "official")

    def test_encrypted_count(self):
        lines = [
            line({"type": "response_item", "payload": {"encrypted_content": "abc"}}),
            line({"type": "response_item", "payload": {}}),
        ]
        self.assertEqual(encrypted_count(lines), 1)


if __name__ == "__main__":
    unittest.main()

