import unittest

from fyk_agent.context import ContextManager


class ContextManagerTests(unittest.TestCase):
    def test_compaction_preserves_fixed_messages_and_recent_protocol_block(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "old"}]},
            {"role": "tool", "tool_call_id": "old", "content": "x" * 2000},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "new"}]},
            {"role": "tool", "tool_call_id": "new", "content": "recent"},
        ]
        manager = ContextManager(max_chars=800)
        compacted = manager.compact(messages)
        self.assertEqual(compacted[0], messages[0])
        self.assertEqual(compacted[1], messages[1])
        self.assertEqual(manager.compactions, 1)
        self.assertEqual(compacted[-2:], messages[-2:])
        self.assertTrue(any(message.get("role") == "system" for message in compacted[2:-2]))

    def test_small_context_is_unchanged(self) -> None:
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        manager = ContextManager(max_chars=1000)
        self.assertIs(manager.compact(messages), messages)


if __name__ == "__main__":
    unittest.main()

