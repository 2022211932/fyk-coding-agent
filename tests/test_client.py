import unittest

from fyk_agent.client import ModelError, OpenAICompatibleClient


class ClientParsingTests(unittest.TestCase):
    def test_parse_normal_tool_reply(self) -> None:
        call = {"id": "1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        reply = OpenAICompatibleClient._parse(
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}
        )
        self.assertEqual(reply.content, "")
        self.assertEqual(reply.tool_calls, [call])

    def test_parse_rejects_unexpected_payload(self) -> None:
        with self.assertRaises(ModelError):
            OpenAICompatibleClient._parse({"error": "bad"})


if __name__ == "__main__":
    unittest.main()

