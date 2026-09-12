"""Cancellation targets and receipts, with no real Modal calls."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_collab_evals.adapters.modal_cleanup import ModalCallCanceller, cancel_retained_dispatch
from agent_collab_evals.canonical import canonical_json_bytes, digest_value


class ModalCleanupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.request = SimpleNamespace(request_digest=digest_value("one request"))
        self.dispatch = {"function_call_id": "fc-testcall", "candidate_manifest_digest": digest_value("candidate")}
        self.evidence = Mock()
        self.evidence.resolve_dispatch.return_value = canonical_json_bytes(self.dispatch)
        self.canceller = Mock()
        self.canceller.cancel.return_value = {"schema_version": "modal-call-cancellation/v1",
            "function_call_id": "fc-testcall", "status": "cancellation_requested"}

    def test_verified_call_cancellation_is_retained_and_idempotent(self):
        first = cancel_retained_dispatch(self.request, self.dispatch, self.evidence, self.canceller, self.root)
        second = cancel_retained_dispatch(self.request, self.dispatch, self.evidence, self.canceller, self.root)
        self.assertEqual(first, second)
        self.assertFalse(first["terminal_confirmed"])
        self.canceller.cancel.assert_called_once_with("fc-testcall")

    def test_missing_dispatch_stays_unresolved_without_cancelling_anything(self):
        result = cancel_retained_dispatch(self.request, None, self.evidence, self.canceller, self.root)
        self.assertEqual(result["status"], "unresolved_dispatch")
        self.canceller.cancel.assert_not_called()

    def test_failed_identity_verification_prevents_cancellation(self):
        self.evidence.resolve_dispatch.side_effect = ValueError("request binding differs")
        with self.assertRaises(ValueError):
            cancel_retained_dispatch(self.request, self.dispatch, self.evidence, self.canceller, self.root)
        self.canceller.cancel.assert_not_called()

    def test_transport_failure_does_not_write_success_receipt(self):
        self.canceller.cancel.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            cancel_retained_dispatch(self.request, self.dispatch, self.evidence, self.canceller, self.root)
        self.assertFalse((self.root / "cleanup").exists())

    def test_cli_is_bounded_and_only_targets_the_function_call(self):
        repository = Path(__file__).resolve().parents[1]
        client = ModalCallCanceller(repository, repository / ".venv/bin/modal")
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0,
                   canonical_json_bytes(self.canceller.cancel.return_value).decode())) as run:
            client.cancel("fc-testcall")
        arguments, options = run.call_args
        self.assertEqual(arguments[0][-1], "fc-testcall")
        self.assertTrue(arguments[0][-2].endswith("scripts/runtime/modal_cancel.py"))
        self.assertEqual(options["timeout"], 30)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
