"""No-spend recovery over real frozen inventories and SQLite execution ledgers."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from agent_collab_evals.adapters.sqlite_compute_routes import ComputeRouteAdapter, SqliteComputeRouteInventory
from agent_collab_evals.canonical import digest_bytes, digest_file, digest_value, parse_json
from agent_collab_evals.compute_backend import ComputeEvidencePointer, ComputeExecutionRequest, ComputeExecutionStatus
from agent_collab_evals.evaluation import EvaluationScope
from agent_collab_evals.pilot_evidence import retain_document
from agent_collab_evals.solo_evaluation_recovery import plan_evaluation_recovery
from tests.test_compute_routes import _AuthorizedTransport
from tests.test_compute_backend import _EvidenceStore, _Transport


class _RetainedEvidence(_EvidenceStore):
    def pointer(self, request, call_id):
        locator = f"evidence/{request.execution_key}.json"
        content = self._documents[locator]
        doc = parse_json(content.decode())
        return (ComputeEvidencePointer(locator, digest_value(doc)),
            ComputeExecutionStatus(doc["status"]), doc["used_seconds"], doc["failure"])


class EvaluationRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.output = self.base / "source", self.base / "recovery"
        self.evidence, self.calls = _RetainedEvidence(), []
        adapter = ComputeRouteAdapter(_Transport(self.evidence).profile_digest, self.evidence.profile_digest,
            lambda root, spend: _AuthorizedTransport(self.evidence, spend, self.calls), lambda root: self.evidence)
        self.inventory = SqliteComputeRouteInventory(self.root / "evaluation/compute", "solo", {"quality": adapter})
        self.candidate = b"synthetic candidate"
        self.complete = ComputeExecutionRequest("hidden:complete", "solo", "reservation", EvaluationScope.HIDDEN,
            digest_bytes(self.candidate), digest_value({"manifest": 1}), digest_value({"evaluator": 1}), 60)
        self.rejected = replace(self.complete, execution_key="hidden:rejected")
        self.missing = replace(self.complete, execution_key="hidden:missing")
        self.route_id = self.inventory.register("quality", (self.complete, self.rejected, self.missing))
        self.backend = self.inventory.backend("quality")
        for request in (self.complete, self.rejected, self.missing):
            self.inventory.authorize(request, approval_reference="synthetic-only")
        for request in (self.complete, self.rejected):
            self.backend.submit(request, self.candidate)
        self.backend.collect(self.complete, timeout_seconds=0)
        # Simulate collection finishing but the old adapter rejecting the terminal
        # document before it can acknowledge the result in the execution ledger.
        source, _ = self.inventory._source(self.route_id)
        transport = source.backend._transport
        transport.terminal_status = ComputeExecutionStatus.FAILED
        transport.failure = "gpu.driver_version differs; gpu_after.driver_version differs"
        transport.poll(self.rejected, f"fc-{self.rejected.request_digest[7:23]}", 0)
        retain_document(self.root / "compute-inventory-seal.json", {"inventory_digest": self.inventory.seal()})
        config_digest = retain_document(self.root / "run-config.json", {"synthetic": True})
        self.audit_digest = retain_document(self.root / "audit.json", {"run_id": "solo", "status": "aborted",
            "scoreable": False, "run_config_digest": config_digest, "budget_reconciliation": {"valid": True}})

    def plan(self):
        return plan_evaluation_recovery(self.root, self.audit_digest,
            lambda adapter, route, manifest: self.evidence, self.output)

    def test_reuse_rejection_and_missing_are_retained_without_new_dispatch_or_source_changes(self):
        before = {path: digest_file(path) for path in self.root.rglob("*") if path.is_file() and not path.name.endswith("-shm")}
        plan = self.plan()
        self.assertEqual(plan["summary"], {"reuse_verified": 1, "replace_environment_rejected": 1, "execute_missing": 1})
        self.assertFalse(plan["execution_authorized"])
        self.assertFalse(plan["scoreable"])
        self.assertEqual(plan["agent_reruns"], 0)
        self.assertEqual(plan["new_provider_calls"], 0)
        self.assertEqual(self.calls, [self.complete.request_digest, self.rejected.request_digest])
        self.assertEqual(before, {path: digest_file(path) for path in before})
        self.assertEqual(self.plan(), plan)  # Write-once, idempotent planning.
        for entry in plan["entries"]:
            if "retained_evidence" in entry:
                ref = entry["retained_evidence"]
                self.assertEqual(digest_file(self.output / ref["file"]), ref["digest"])
        self.assertIs(self.backend._backend(self.rejected).inspect(self.rejected).status, ComputeExecutionStatus.DISPATCHED)

    def test_missing_retained_bundle_is_uncertain_not_a_retry(self):
        del self.evidence._documents[f"evidence/{self.rejected.execution_key}.json"]
        with self.assertRaises(KeyError):
            self.plan()
        self.assertFalse((self.output / "plan.json").exists())

    def test_coherent_source_audit_edit_is_rejected_by_independent_pin(self):
        (self.root / "audit.json").write_text('{"status":"aborted","scoreable":false}')
        with self.assertRaisesRegex(RuntimeError, "audit digest"):
            self.plan()

    def test_consumed_missing_authority_blocks_retry(self):
        route = self.root / "evaluation/compute/routes" / self.route_id
        with closing(sqlite3.connect(route / "spend.sqlite3")) as connection:
            connection.execute("UPDATE compute_spend_authorizations SET status='consumed' WHERE request_digest=?",
                (self.missing.request_digest,))
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "delivery is uncertain"):
            self.plan()

    def test_corrupt_complete_evidence_is_not_reused(self):
        self.evidence._documents[f"evidence/{self.complete.execution_key}.json"] = b'{}'
        with self.assertRaisesRegex(RuntimeError, "evidence bytes"):
            self.plan()

    def test_source_overlap_and_ordinary_quality_failure_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            plan_evaluation_recovery(self.root, self.audit_digest, lambda *args: self.evidence, self.root / "recovery")
        locator = f"evidence/{self.rejected.execution_key}.json"
        doc = parse_json(self.evidence._documents[locator].decode())
        doc["failure"] = "quality degraded"
        self.evidence.put(locator, doc)
        with self.assertRaisesRegex(RuntimeError, "explicit recovery policy"):
            self.plan()
