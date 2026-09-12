"""No-spend exact routing, durable approval, and reconstruction checks."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agent_collab_evals.adapters.sqlite_compute_routes import ComputeRouteAdapter, SqliteComputeRouteInventory
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


if __name__ == "__main__":
    unittest.main()
