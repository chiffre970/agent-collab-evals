from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_collab_evals.adapters.local_events import LocalEventSink


class LocalEventSinkTests(unittest.TestCase):
    def test_independent_sink_instances_serialize_concurrent_appends(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def append(index: int) -> None:
                LocalEventSink(root).append("run-1", "concurrent", {"index": index})

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(append, range(32)))

            events = LocalEventSink(root).read("run-1")
            self.assertEqual([event["sequence"] for event in events], list(range(1, 33)))
            self.assertEqual(
                {event["payload"]["index"] for event in events}, set(range(32))
            )

    def test_reopen_continues_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            LocalEventSink(root).append("run-1", "first", {})
            sequence = LocalEventSink(root).append("run-1", "second", {})

            self.assertEqual(sequence, 2)
            self.assertEqual(
                [event["sequence"] for event in LocalEventSink(root).read("run-1")],
                [1, 2],
            )

    def test_non_monotonic_existing_log_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "run-1" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "sequence": 7,
                        "campaign_run_id": "run-1",
                        "kind": "corrupt",
                        "payload": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "non-monotonic"):
                LocalEventSink(root).append("run-1", "next", {})

    def test_mismatched_campaign_in_existing_log_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "run-1" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "sequence": 1,
                        "campaign_run_id": "another-run",
                        "kind": "corrupt",
                        "payload": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "identifier mismatch"):
                LocalEventSink(root).read("run-1")


if __name__ == "__main__":
    unittest.main()
