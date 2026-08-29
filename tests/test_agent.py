from pathlib import Path
import tempfile
import unittest

from fyk_agent.agent import CodingAgent
from fyk_agent.client import AssistantReply
from fyk_agent.tools import ToolRegistry
from fyk_agent.workspace import Workspace


class FakeClient:
    def __init__(self, replies: list[AssistantReply]):
        self.replies = replies
        self.requests: list[list[dict]] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> AssistantReply:
        self.requests.append([dict(message) for message in messages])
        if not self.replies:
            raise AssertionError("Fake client received more calls than expected")
        return self.replies.pop(0)


def tool_reply(arguments: str = '{"path":"."}') -> AssistantReply:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "list_files", "arguments": arguments},
    }
    return AssistantReply("", [call], {"role": "assistant", "content": None, "tool_calls": [call]})


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
        self.registry = ToolRegistry(Workspace(root), approve=lambda _name, _args: True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_tool_round_trip_then_final_answer(self) -> None:
        final = AssistantReply("Inspected the project.", [], {"role": "assistant", "content": "Inspected the project."})
        client = FakeClient([tool_reply(), final])
        result = CodingAgent(client, self.registry).run("Inspect this repository")
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.steps, 2)
        second_request = client.requests[1]
        self.assertEqual(second_request[-1]["role"], "tool")
        self.assertIn('"ok": true', second_request[-1]["content"])

    def test_invalid_json_is_returned_to_model(self) -> None:
        final = AssistantReply("Recovered.", [], {"role": "assistant", "content": "Recovered."})
        client = FakeClient([tool_reply("{bad json"), final])
        CodingAgent(client, self.registry).run("Try a malformed call")
        self.assertIn("invalid_tool_call", client.requests[1][-1]["content"])

    def test_step_limit_stops_loop(self) -> None:
        client = FakeClient([tool_reply(), tool_reply()])
        result = CodingAgent(client, self.registry, max_steps=2).run("Keep inspecting")
        self.assertEqual(result.stop_reason, "step_limit")
        self.assertEqual(result.steps, 2)


if __name__ == "__main__":
    unittest.main()

