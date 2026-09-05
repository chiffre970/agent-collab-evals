from __future__ import annotations

import json
import unittest

from agent_collab_evals.adapters.deterministic_model import (
    DeterministicModelUpstream,
    DeterministicToolModelUpstream,
)


class DeterministicModelUpstreamTests(unittest.TestCase):
    def test_returns_a_reconcilable_stream_and_records_a_defensive_copy(self) -> None:
        upstream = DeterministicModelUpstream(
            model="provider/model",
            provider="Provider",
            content="DONE",
        )
        request = {"model": "provider/model", "messages": []}

        response = upstream.stream(json.dumps(request).encode("utf-8"))
        request["model"] = "changed"
        body = b"".join(response.chunks)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.provider_name, "Provider")
        self.assertIn(b'"content":"DONE"', body)
        self.assertIn(b'"prompt_tokens":10', body)
        self.assertTrue(body.endswith(b"data: [DONE]\n\n"))
        self.assertEqual(upstream.requests[0]["model"], "provider/model")

    def test_rejects_nonobject_requests(self) -> None:
        upstream = DeterministicModelUpstream(model="model", provider="provider")

        with self.assertRaisesRegex(ValueError, "must be an object"):
            upstream.stream(b"[]")

    def test_tool_model_exercises_native_task_once(self) -> None:
        upstream = DeterministicToolModelUpstream(
            model="provider/model", provider="Provider", peer_actor_count=1
        )
        request = {
            "model": "provider/model",
            "messages": [{"role": "user", "content": "delegate"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "task",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "prompt": {"type": "string"},
                                "subagent_type": {"type": "string"},
                            },
                            "required": [
                                "description",
                                "prompt",
                                "subagent_type",
                            ],
                        },
                    },
                }
            ],
        }

        first = b"".join(upstream.stream(json.dumps(request).encode()).chunks)
        self.assertIn(b'"name":"task"', first)
        self.assertIn(b"subagent_type", first)
        self.assertIn(b"general", first)

        request["messages"].append({"role": "tool", "content": "CHILD_OK"})
        second = b"".join(upstream.stream(json.dumps(request).encode()).chunks)
        self.assertIn(b'"content":"RUNTIME_OK"', second)

    def test_tool_model_publishes_then_reads_peers(self) -> None:
        upstream = DeterministicToolModelUpstream(
            model="provider/model", provider="Provider", peer_actor_count=1
        )
        request = {
            "model": "provider/model",
            "messages": [{"role": "user", "content": "collaborate"}],
            "tools": [
                {"type": "function", "function": {"name": "peer_publish"}},
                {"type": "function", "function": {"name": "peer_list_recent"}},
            ],
        }

        published = b"".join(
            upstream.stream(json.dumps(request).encode()).chunks
        )
        self.assertIn(b'"name":"peer_publish"', published)

        request["messages"].extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "peer_publish"}}
                    ],
                },
                {"role": "tool", "content": "published"},
            ]
        )
        finished_first = b"".join(
            upstream.stream(json.dumps(request).encode()).chunks
        )
        self.assertIn(b'"content":"PEER_TOOLS_OK"', finished_first)

        request["messages"].append(
            {"role": "user", "content": "read collaboration"}
        )
        listed = b"".join(upstream.stream(json.dumps(request).encode()).chunks)
        self.assertIn(b'"name":"peer_list_recent"', listed)

        request["messages"].extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "peer_list_recent"}}
                    ],
                },
                {"role": "tool", "content": "listed"},
            ]
        )
        completed = b"".join(
            upstream.stream(json.dumps(request).encode()).chunks
        )
        self.assertIn(b'"content":"PEER_TOOLS_OK"', completed)


if __name__ == "__main__":
    unittest.main()
