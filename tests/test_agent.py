from pathlib import Path
import tempfile
import threading
import time
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


class SlowClient:
    def __init__(self) -> None:
        self.started = threading.Event()

    def complete(self, messages: list[dict], tools: list[dict]) -> AssistantReply:
        self.started.set()
        time.sleep(2)
        return AssistantReply("too late", [], {"role": "assistant", "content": "too late"})


def tool_reply(arguments: str = '{"path":"."}') -> AssistantReply:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "list_files", "arguments": arguments},
    }
    return AssistantReply("", [call], {"role": "assistant", "content": None, "tool_calls": [call]})


def named_tool_reply(name: str, arguments: dict, call_id: str) -> AssistantReply:
    call = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
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

    def test_second_prompt_continues_the_same_conversation(self) -> None:
        first_reply = AssistantReply("First answer", [], {"role": "assistant", "content": "First answer"})
        second_reply = AssistantReply("Second answer", [], {"role": "assistant", "content": "Second answer"})
        client = FakeClient([first_reply, second_reply])
        agent = CodingAgent(client, self.registry)
        first = agent.run("First prompt")
        second = agent.run("Follow-up prompt", history=first.messages)
        second_request = client.requests[1]
        self.assertEqual(
            [(message["role"], message.get("content")) for message in second_request],
            [
                ("system", second_request[0]["content"]),
                ("user", "First prompt"),
                ("assistant", "First answer"),
                ("user", "Follow-up prompt"),
            ],
        )
        self.assertEqual(second.final_text, "Second answer")

    def test_invalid_conversation_history_is_rejected(self) -> None:
        client = FakeClient([])
        with self.assertRaisesRegex(ValueError, "system message"):
            CodingAgent(client, self.registry).run(
                "Continue", history=[{"role": "user", "content": "orphan"}]
            )

    def test_clear_context_resets_compaction_counter(self) -> None:
        agent = CodingAgent(FakeClient([]), self.registry)
        agent.context.compactions = 4
        agent.clear_context()
        self.assertEqual(agent.context.compactions, 0)

    def test_reports_real_context_statistics(self) -> None:
        events: list[tuple[str, dict]] = []
        client = FakeClient([AssistantReply("Done", [], {"role": "assistant", "content": "Done"})])
        result = CodingAgent(
            client,
            self.registry,
            max_context_chars=12_345,
            notify=lambda kind, data: events.append((kind, data)),
        ).run("Measure this conversation")
        context_events = [data for kind, data in events if kind == "context_stats"]
        self.assertGreaterEqual(len(context_events), 2)
        self.assertEqual(context_events[-1]["message_count"], len(result.messages))
        self.assertEqual(context_events[-1]["max_context_chars"], 12_345)
        self.assertGreater(context_events[-1]["context_chars"], 0)

    def test_model_wait_can_be_cancelled(self) -> None:
        cancelled = threading.Event()
        client = SlowClient()
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                CodingAgent(client, self.registry, cancelled=cancelled.is_set).run("Wait")
            )
        )
        worker.start()
        self.assertTrue(client.started.wait(timeout=1))
        cancelled.set()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].stop_reason, "cancelled")

    def test_evidence_bound_plan_completes_with_real_tool_result(self) -> None:
        plan = [
            {
                "id": "inspect",
                "title": "Inspect repository",
                "kind": "inspect",
                "status": "in_progress",
            }
        ]
        completed_plan = [
            {
                **plan[0],
                "status": "completed",
                "evidence_ids": ["evidence-files-1"],
            }
        ]
        client = FakeClient(
            [
                named_tool_reply("update_plan", {"summary": "Inspect", "steps": plan}, "plan-1"),
                named_tool_reply("list_files", {"path": "."}, "files-1"),
                named_tool_reply(
                    "update_plan",
                    {"summary": "Inspect", "steps": completed_plan},
                    "plan-2",
                ),
                AssistantReply("Complete with evidence.", [], {"role": "assistant", "content": "Complete with evidence."}),
            ]
        )
        emitted: list[tuple[str, dict]] = []
        result = CodingAgent(
            client,
            self.registry,
            notify=lambda kind, data: emitted.append((kind, data)),
        ).run("Inspect this repository carefully")
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.final_text, "Complete with evidence.")
        evidence_result = client.requests[2][-1]["content"]
        self.assertIn('"evidence_id": "evidence-files-1"', evidence_result)
        plan_events = [data for kind, data in emitted if kind == "plan_updated"]
        self.assertEqual(plan_events[-1]["plan"]["completed"], 1)

    def test_unfinished_plan_prevents_false_completion(self) -> None:
        pending_plan = [
            {
                "id": "verify",
                "title": "Run tests",
                "kind": "verify",
                "status": "pending",
            }
        ]
        client = FakeClient(
            [
                named_tool_reply(
                    "update_plan",
                    {"summary": "Verify", "steps": pending_plan},
                    "plan-1",
                ),
                AssistantReply("Everything passed.", [], {"role": "assistant", "content": "Everything passed."}),
                AssistantReply("It is complete.", [], {"role": "assistant", "content": "It is complete."}),
            ]
        )
        result = CodingAgent(client, self.registry, max_steps=3).run("Run the tests")
        self.assertEqual(result.stop_reason, "incomplete_plan")
        self.assertIn("Run tests", result.final_text)
        self.assertEqual(client.requests[2][-1]["role"], "system")

    def test_blocked_plan_has_distinct_stop_reason(self) -> None:
        client = FakeClient(
            [
                named_tool_reply(
                    "update_plan",
                    {
                        "summary": "Unavailable task",
                        "steps": [
                            {
                                "id": "blocked",
                                "title": "Contact service",
                                "kind": "other",
                                "status": "blocked",
                                "note": "Network access is unavailable",
                                "blocker_type": "user_input_required",
                            }
                        ],
                    },
                    "plan-1",
                ),
                AssistantReply("Blocked by the environment.", [], {"role": "assistant", "content": "Blocked by the environment."}),
            ]
        )
        result = CodingAgent(client, self.registry).run("Contact the service")
        self.assertEqual(result.stop_reason, "blocked")
        self.assertTrue(result.final_text.startswith("任务未完成："))


if __name__ == "__main__":
    unittest.main()
