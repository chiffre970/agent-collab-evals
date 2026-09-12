"""No-spend exact routing, durable approval, and reconstruction checks."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from agent_collab_evals.adapters.sqlite_compute_routes import ComputeRouteAdapter, SqliteComputeRouteInventory
from agent_collab_evals.adapters.sqlite_execution_backend import SqliteComputeBackend
from agent_collab_evals.canonical import digest_bytes, digest_value
from agent_collab_evals.compute_backend import ComputeExecutionRequest, ComputeExecutionStatus
from agent_collab_evals.evaluation import EvaluationScope
from tests.test_compute_backend import _EvidenceStore, _Transport


class _AuthorizedTransport(_Transport):
    def __init__(self, evidence, spend, calls):
        super().__init__(evidence)
        self.spend, self.calls = spend, calls

    def dispatch(self, request, candidate):
        self.spend.consume(request, self.profile_digest)
        self.calls.append(request.request_digest)
        return super().dispatch(request, candidate)

    def cleanup(self, request, canceller):
        return canceller.cancel(f"fc-{request.request_digest[7:23]}")


class ComputeRouteTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.evidence = _EvidenceStore()
        self.calls = []
        self.adapter = ComputeRouteAdapter(
            _Transport(self.evidence).profile_digest, self.evidence.profile_digest,
            lambda root, spend: _AuthorizedTransport(self.evidence, spend, self.calls),
            lambda root: self.evidence,
        )
        self.inventory = self.open()
        self.candidate = b"synthetic candidate"
        self.request = ComputeExecutionRequest("visible:one", "solo", "reservation", EvaluationScope.VISIBLE,
            digest_bytes(self.candidate), digest_value({"manifest": 1}), digest_value({"evaluator": 1}), 60)

    def open(self, digest=None):
        return SqliteComputeRouteInventory(self.root, "solo", {"public": self.adapter}, expected_seal_digest=digest)

    def test_reconstruct_collect_and_authority_remain_single_use(self):
        route_id = self.inventory.register("public", (self.request,))
        self.assertEqual(self.inventory.register("public", (self.request,)), route_id)
        token = self.inventory.authorize(self.request, approval_reference="test-only-no-spend")
        backend = self.inventory.backend("public")
        self.assertIs(backend.submit(self.request, self.candidate).status, ComputeExecutionStatus.DISPATCHED)
        digest = self.inventory.seal()
        restored = self.open(digest)
        self.assertEqual(restored.authorize(self.request, approval_reference="test-only-no-spend"), token)
        backend = restored.backend("public")
        backend.submit(self.request, self.candidate)
        backend.collect(self.request, timeout_seconds=1)
        self.assertEqual(len(backend.reconcile("solo")), 1)
        self.assertEqual(len(restored.sources()), 1)
        self.assertEqual(self.calls, [self.request.request_digest])

    def test_route_does_not_grant_dispatch_approval(self):
        self.inventory.register("public", (self.request,))
        result = self.inventory.backend("public").submit(self.request, self.candidate)
        self.assertIs(result.status, ComputeExecutionStatus.FAILED)
        self.assertEqual(self.calls, [])

    def test_missing_route_and_unsealed_inventory_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "no retained route"):
            self.inventory.backend("public").submit(self.request, self.candidate)
        self.inventory.register("public", (self.request,))
        with self.assertRaisesRegex(RuntimeError, "sealed"):
            self.inventory.sources()
        self.inventory.seal()
        with self.assertRaisesRegex(RuntimeError, "sealed"):
            self.inventory.register("public", (replace(self.request, execution_key="visible:two"),))
        with self.assertRaisesRegex(RuntimeError, "seal"):
            self.open(digest_value({"wrong_seal": True}))

    def test_unsealed_cleanup_only_cancels_consumed_unfinished_requests(self):
        pending = self.request
        complete = replace(pending, execution_key="visible:complete")
        untouched = replace(pending, execution_key="visible:untouched")
        self.inventory.register("public", (pending, complete, untouched))
        backend = self.inventory.backend("public")
        for request in (pending, complete):
            self.inventory.authorize(request, approval_reference="test-only")
            backend.submit(request, self.candidate)
        backend.collect(complete, timeout_seconds=1)
        canceller = Mock()
        canceller.cancel.return_value = {"status": "cancellation_requested", "terminal_confirmed": False}
        results = {item["request_digest"]: item for item in self.open().cleanup(canceller)}
        self.assertEqual(results[pending.request_digest]["status"], "cancellation_requested")
        self.assertEqual(results[complete.request_digest]["status"], "terminal_evidence_verified")
        self.assertEqual(results[untouched.request_digest]["status"], "not_dispatched")
        canceller.cancel.assert_called_once_with(f"fc-{pending.request_digest[7:23]}")

    def test_cleanup_failure_does_not_skip_the_next_call(self):
        requests = (self.request, replace(self.request, execution_key="visible:two"))
        self.inventory.register("public", requests)
        for request in requests:
            self.inventory.authorize(request, approval_reference="test-only")
            self.inventory.backend("public").submit(request, self.candidate)
        canceller = Mock()
        canceller.cancel.side_effect = [TimeoutError(), {"status": "cancellation_requested"}]
        result = self.inventory.cleanup(canceller)
        self.assertEqual(canceller.cancel.call_count, 2)
        self.assertEqual({item["status"] for item in result}, {"cleanup_failed", "cancellation_requested"})

    def test_unverifiable_dispatch_does_not_reach_cancellation(self):
        self.inventory.register("public", (self.request,))
        self.inventory.authorize(self.request, approval_reference="test-only")
        self.inventory.backend("public").submit(self.request, self.candidate)
        canceller = Mock()
        with patch.object(SqliteComputeBackend, "validate_cleanup_dispatch", side_effect=RuntimeError("unverifiable")):
            result = self.inventory.cleanup(canceller)
        self.assertEqual(result[0]["status"], "cleanup_failed")
        canceller.cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
