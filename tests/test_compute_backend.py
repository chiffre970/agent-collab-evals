from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_collab_evals.adapters.sqlite_execution_backend import (
    SqliteComputeBackend,
)
from agent_collab_evals.adapters.no_compute_reconciliation import (
    NoComputeExecutionReconciler,
)
from agent_collab_evals.canonical import (
    canonical_json_bytes,
    digest_bytes,
    digest_value,
)
from agent_collab_evals.compute_backend import (
    ComputeEvidencePointer,
    ComputeExecutionRequest,
    ComputeExecutionStatus,
    ExternalDispatch,
    FrozenComputeRunManifest,
    TransportPoll,
)
from agent_collab_evals.evaluation import EvaluationScope


class _EvidenceStore:
    def __init__(self) -> None:
        self._documents: dict[str, bytes] = {}
        self._dispatches: dict[str, bytes] = {}
        self._profile_digest = digest_value({"adapter": "fake-evidence/v1"})

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def put(self, locator: str, document: dict[str, object]) -> ComputeEvidencePointer:
        content = canonical_json_bytes(document)
        self._documents[locator] = content
        return ComputeEvidencePointer(locator, digest_bytes(content))

    def resolve(self, pointer: ComputeEvidencePointer) -> bytes:
        try:
            return self._documents[pointer.locator]
        except KeyError as error:
            raise RuntimeError("fake evidence is unavailable") from error

    def resolve_dispatch(
        self, request: ComputeExecutionRequest, external_call_id: str
    ) -> bytes:
        try:
            return self._dispatches[external_call_id]
        except KeyError as error:
            raise RuntimeError("fake dispatch evidence is unavailable") from error


class _Transport:
    def __init__(self, evidence: _EvidenceStore) -> None:
        self.evidence = evidence
        self.dispatch_count = 0
        self.poll_count = 0
        self.block_dispatch = False
        self.dispatch_started = threading.Event()
        self.release_dispatch = threading.Event()
        self.raise_dispatch: Exception | None = None
        self.timeout_once = False
        self.terminal_status = ComputeExecutionStatus.COMPLETE
        self.failure: str | None = None
        self.used_seconds = 7
        self._profile_digest = digest_value({"adapter": "fake-transport/v1"})

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    def dispatch(
        self, request: ComputeExecutionRequest, candidate: bytes
    ) -> ExternalDispatch:
        self.dispatch_count += 1
        self.dispatch_started.set()
        if self.block_dispatch:
            self.release_dispatch.wait(timeout=5)
        if self.raise_dispatch is not None:
            raise self.raise_dispatch
        external_call_id = f"fc-{request.request_digest[7:23]}"
        evidence = canonical_json_bytes(
            {
                "request_digest": request.request_digest,
                "candidate_digest": digest_bytes(candidate),
                "external_call_id": external_call_id,
            }
        )
        self.evidence._dispatches[external_call_id] = evidence
        return ExternalDispatch(external_call_id, digest_bytes(evidence))

    def poll(
        self,
        request: ComputeExecutionRequest,
        external_call_id: str,
        timeout_seconds: int,
    ) -> TransportPoll:
        self.poll_count += 1
        if self.timeout_once and self.poll_count == 1:
            raise TimeoutError("still running")
        used_seconds = (
            self.used_seconds
            if self.terminal_status is ComputeExecutionStatus.COMPLETE
            else 60
        )
        document = {
            "schema_version": "compute-execution-evidence/v0alpha1",
            "request_digest": request.request_digest,
            "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest,
            "transport_profile_digest": self.profile_digest,
            "evidence_profile_digest": self.evidence.profile_digest,
            "external_call_id": external_call_id,
            "status": self.terminal_status.value,
            "used_seconds": used_seconds,
            "failure": self.failure,
            "result": {
                "eligible": self.terminal_status
                is ComputeExecutionStatus.COMPLETE,
                "criterion_units": 1_002_000,
            },
        }
        pointer = self.evidence.put(
            f"evidence/{request.execution_key}.json", document
        )
        return TransportPoll(
            self.terminal_status,
            pointer,
            used_seconds,
            self.failure,
        )


class ComputeBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.candidate = b'{"candidate":"pinned"}'
        self.request = ComputeExecutionRequest(
            execution_key="visible:candidate-1",
            campaign_run_id="campaign-run",
            reservation_id="evaluation-" + "1" * 32,
            scope=EvaluationScope.VISIBLE,
            candidate_digest=digest_bytes(self.candidate),
            candidate_manifest_digest=digest_value({"candidate": "pinned"}),
            evaluator_profile_digest=digest_value({"evaluator": "v1"}),
            maximum_seconds=60,
        )
        self.evidence = _EvidenceStore()
        self.transport = _Transport(self.evidence)
        self.manifest = FrozenComputeRunManifest.load_or_create(
            self.root / "compute-run-manifest.json",
            campaign_run_id=self.request.campaign_run_id,
            compute_enabled=True,
            transport_profile_digest=self.transport.profile_digest,
            backend_profile_digest=SqliteComputeBackend.profile_digest_for(
                self.transport.profile_digest, self.evidence.profile_digest
            ),
            requests=(self.request,),
        )
        self.backend = self._backend()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_dispatch_collect_resolve_and_restart_are_idempotent(self) -> None:
        dispatched = self.backend.submit(self.request, self.candidate)
        self.assertEqual(dispatched.status, ComputeExecutionStatus.DISPATCHED)
        restarted = self._backend()
        self.assertEqual(
            restarted.submit(self.request, self.candidate), dispatched
        )
        completed = restarted.collect(self.request, timeout_seconds=0)
        self.assertEqual(completed.status, ComputeExecutionStatus.COMPLETE)
        receipt, document = restarted.resolve(self.request)
        self.assertEqual(receipt, completed)
        self.assertEqual(document["result"]["criterion_units"], 1_002_000)
        self.assertEqual(self.transport.dispatch_count, 1)
        self.assertEqual(restarted.reconcile("campaign-run"), (completed,))

    def test_reconciliation_reconstructs_requests_from_frozen_manifest(self) -> None:
        self.backend.submit(self.request, self.candidate)
        completed = self.backend.collect(self.request, timeout_seconds=0)

        restarted = self._backend()

        self.assertEqual(restarted.reconcile("campaign-run"), (completed,))

    def test_reconciliation_rejects_missing_execution_and_manifest_tampering(
        self,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "frozen run plan"):
            self.backend.reconcile("campaign-run")

        original = self.manifest.path.read_bytes()
        self.manifest.path.write_bytes(original + b" ")
        with self.assertRaisesRegex(RuntimeError, "changed after registration"):
            self.backend.submit(self.request, self.candidate)

    def test_no_compute_reconciler_rejects_compute_enabled_manifest(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "enables compute"):
            NoComputeExecutionReconciler(self.manifest)

    def test_concurrent_backends_create_only_one_external_dispatch(self) -> None:
        self.transport.block_dispatch = True
        second = self._backend()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_call = executor.submit(
                self.backend.submit, self.request, self.candidate
            )
            self.assertTrue(self.transport.dispatch_started.wait(timeout=2))
            observed = second.submit(self.request, self.candidate)
            self.assertEqual(observed.status, ComputeExecutionStatus.DISPATCHING)
            self.transport.release_dispatch.set()
            completed_dispatch = first_call.result()
        self.assertEqual(
            completed_dispatch.status, ComputeExecutionStatus.DISPATCHED
        )
        self.assertEqual(self.transport.dispatch_count, 1)

    def test_collection_timeout_does_not_redispatch(self) -> None:
        self.transport.timeout_once = True
        self.backend.submit(self.request, self.candidate)
        pending = self.backend.collect(self.request, timeout_seconds=0)
        self.assertEqual(pending.status, ComputeExecutionStatus.DISPATCHED)
        completed = self.backend.collect(self.request, timeout_seconds=0)
        self.assertEqual(completed.status, ComputeExecutionStatus.COMPLETE)
        self.assertEqual(self.transport.dispatch_count, 1)

    def test_ambiguous_dispatch_fails_closed_without_retry(self) -> None:
        self.transport.raise_dispatch = ConnectionError("response lost")
        ambiguous = self.backend.submit(self.request, self.candidate)
        self.assertEqual(ambiguous.status, ComputeExecutionStatus.AMBIGUOUS)
        repeated = self.backend.submit(self.request, self.candidate)
        self.assertEqual(repeated, ambiguous)
        self.assertEqual(self.transport.dispatch_count, 1)
        with self.assertRaisesRegex(RuntimeError, "not reconcilable"):
            self.backend.reconcile("campaign-run")

    def test_terminal_failure_retains_verifiable_evidence(self) -> None:
        self.transport.terminal_status = ComputeExecutionStatus.FAILED
        self.transport.failure = "remote candidate failed"
        self.backend.submit(self.request, self.candidate)
        failed = self.backend.collect(self.request, timeout_seconds=0)
        self.assertEqual(failed.status, ComputeExecutionStatus.FAILED)
        _, document = self.backend.resolve(self.request)
        self.assertEqual(document["failure"], "remote candidate failed")

    def test_compute_overrun_remains_visible_and_invalidates_reconciliation(self) -> None:
        self.transport.used_seconds = 61
        self.backend.submit(self.request, self.candidate)
        with self.assertRaisesRegex(RuntimeError, "exceeds its reservation"):
            self.backend.collect(self.request, timeout_seconds=0)
        with self.assertRaisesRegex(RuntimeError, "not reconcilable: dispatched"):
            self.backend.reconcile("campaign-run")

    def test_database_and_evidence_tampering_fail_reconciliation(self) -> None:
        self.backend.submit(self.request, self.candidate)
        self.backend.collect(self.request, timeout_seconds=0)
        with closing(
            sqlite3.connect(self.root / "executions.sqlite3")
        ) as connection:
            original_dispatch_digest = connection.execute(
                "SELECT dispatch_evidence_digest FROM compute_executions"
            ).fetchone()[0]
            connection.execute(
                "UPDATE compute_executions SET dispatch_evidence_digest = ?",
                ("sha256:" + "0" * 64,),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "dispatch evidence digest"):
            self.backend.reconcile("campaign-run")
        with closing(
            sqlite3.connect(self.root / "executions.sqlite3")
        ) as connection:
            connection.execute(
                "UPDATE compute_executions SET dispatch_evidence_digest = ?",
                (original_dispatch_digest,),
            )
            connection.commit()

        with closing(
            sqlite3.connect(self.root / "executions.sqlite3")
        ) as connection:
            connection.execute(
                "UPDATE compute_executions SET used_seconds = 999"
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "reservation|identity differs"):
            self.backend.resolve(self.request)

        with closing(
            sqlite3.connect(self.root / "executions.sqlite3")
        ) as connection:
            connection.execute(
                "UPDATE compute_executions SET used_seconds = 7"
            )
            connection.commit()
        pointer = self.backend.collect(self.request, timeout_seconds=0).evidence
        assert pointer is not None
        self.evidence._documents[pointer.locator] = b'{"tampered":true}'
        with self.assertRaisesRegex(RuntimeError, "differ from their pointer"):
            self.backend.resolve(self.request)

    def _backend(self) -> SqliteComputeBackend:
        authority = FrozenComputeRunManifest.load(
            self.manifest.path,
            expected_digest=self.manifest.manifest_digest,
        )
        return SqliteComputeBackend(
            self.root / "executions.sqlite3",
            self.transport,
            self.evidence,
            authority,
        )


if __name__ == "__main__":
    unittest.main()
