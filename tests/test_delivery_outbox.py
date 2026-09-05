from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from agent_collab_evals.adapters.fake_harness import FakeHarnessRuntime
from agent_collab_evals.adapters.sqlite_delivery import SqliteDeliveryOutbox
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.domain import (
    AgentIdentity,
    CoordinationCondition,
    Job,
    OrganisationSpec,
)


def _job(materials: str = "materials") -> Job:
    return Job(
        "job-001",
        "Complete the assigned work.",
        digest_value({"materials": materials}),
        {"brief.txt": materials},
    )


class SqliteDeliveryOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "delivery.sqlite3"
        self.outbox = SqliteDeliveryOutbox(self.database)
        self.runtime = FakeHarnessRuntime()
        self.campaign_run_id = "delivery-run"
        organisation = self.runtime.start_organisation(
            OrganisationSpec(
                self.campaign_run_id,
                CoordinationCondition.PEER_ISOLATED,
                2,
                self.root / "workspace",
                "fake://model",
            )
        )
        self.sessions = tuple(
            self.runtime.create_primary(
                organisation, AgentIdentity(self.campaign_run_id, ordinal)
            )
            for ordinal in range(2)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_read_job_preserves_the_complete_materialized_document(self) -> None:
        job = _job()
        self.outbox.prepare(self.campaign_run_id, self.sessions, job)
        self.assertEqual(self.outbox.read_job(self.campaign_run_id, job.job_id), job)
        with self.assertRaises(KeyError):
            self.outbox.read_job(self.campaign_run_id, "not-prepared")

    def test_complete_delivery_reconciles_exact_jobs_sessions_and_receipts(
        self,
    ) -> None:
        job = _job()
        intents = self.outbox.prepare(self.campaign_run_id, self.sessions, job)
        receipts = tuple(
            self.outbox.acknowledge(
                intent, self.runtime.deliver(intent.session, intent.job)
            )
            for intent in intents
        )

        completed = self.outbox.complete(self.campaign_run_id, job.job_id)
        reconciled = self.outbox.reconcile(
            self.campaign_run_id, self.sessions, (job.job_id,)
        )

        self.assertEqual(completed, receipts)
        self.assertEqual(reconciled.receipts, receipts)
        self.assertEqual(reconciled.job_ids, (job.job_id,))
        self.assertEqual(
            self.outbox.completed_job_ids(self.campaign_run_id), (job.job_id,)
        )

    def test_partial_delivery_restarts_without_repeating_acknowledged_recipient(
        self,
    ) -> None:
        job = _job()
        intents = self.outbox.prepare(self.campaign_run_id, self.sessions, job)
        first_receipt = self.runtime.deliver(intents[0].session, job)
        self.outbox.acknowledge(intents[0], first_receipt)

        with self.assertRaisesRegex(RuntimeError, "receipt is pending"):
            self.outbox.complete(self.campaign_run_id, job.job_id)

        restarted = SqliteDeliveryOutbox(self.database)
        repeated = restarted.prepare(self.campaign_run_id, self.sessions, job)
        self.assertEqual(restarted.acknowledged(repeated[0]), first_receipt)
        self.assertIsNone(restarted.acknowledged(repeated[1]))
        second_receipt = self.runtime.deliver(repeated[1].session, job)
        restarted.acknowledge(repeated[1], second_receipt)

        self.assertEqual(
            restarted.complete(self.campaign_run_id, job.job_id),
            (first_receipt, second_receipt),
        )

    def test_conflicting_retry_and_wrong_receipt_fail_closed(self) -> None:
        job = _job()
        intents = self.outbox.prepare(self.campaign_run_id, self.sessions, job)

        with self.assertRaisesRegex(RuntimeError, "job already differs"):
            self.outbox.prepare(
                self.campaign_run_id, self.sessions, _job("changed")
            )
        wrong = self.runtime.deliver(self.sessions[1], job)
        with self.assertRaisesRegex(RuntimeError, "receipt differs from intent"):
            self.outbox.acknowledge(intents[0], wrong)

    def test_concurrent_prepare_is_idempotent(self) -> None:
        job = _job()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = tuple(
                executor.map(
                    lambda _: self.outbox.prepare(
                        self.campaign_run_id, self.sessions, job
                    ),
                    range(8),
                )
            )

        self.assertTrue(all(result == results[0] for result in results))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM delivery_jobs").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM delivery_intents").fetchone()[0],
                2,
            )

    def test_receipt_tampering_fails_reconciliation(self) -> None:
        job = _job()
        intents = self.outbox.prepare(self.campaign_run_id, self.sessions, job)
        for intent in intents:
            self.outbox.acknowledge(
                intent, self.runtime.deliver(intent.session, intent.job)
            )
        self.outbox.complete(self.campaign_run_id, job.job_id)

        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE delivery_intents SET receipt_json = '{}' "
                "WHERE intent_id = ?",
                (intents[0].intent_id,),
            )
            connection.commit()

        with self.assertRaisesRegex(RuntimeError, "receipt digest differs"):
            self.outbox.reconcile(
                self.campaign_run_id, self.sessions, (job.job_id,)
            )

    def test_audit_tampering_fails_reconciliation(self) -> None:
        job = _job()
        intents = self.outbox.prepare(self.campaign_run_id, self.sessions, job)
        for intent in intents:
            self.outbox.acknowledge(
                intent, self.runtime.deliver(intent.session, intent.job)
            )
        self.outbox.complete(self.campaign_run_id, job.job_id)

        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE delivery_audit SET payload_digest = ? WHERE sequence = 1",
                ("sha256:" + "0" * 64,),
            )
            connection.commit()

        with self.assertRaisesRegex(RuntimeError, "audit record differs"):
            self.outbox.reconcile(
                self.campaign_run_id, self.sessions, (job.job_id,)
            )


if __name__ == "__main__":
    unittest.main()
