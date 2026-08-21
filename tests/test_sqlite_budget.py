from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_collab_evals.adapters.sqlite_budget import SqliteBudgetAccount
from agent_collab_evals.budget import (
    ActorBudgetAllocation,
    BillingRateCard,
    BudgetRejected,
    BudgetReservation,
    ModelCallContext,
    ProviderUsage,
)


def _rate_card() -> BillingRateCard:
    return BillingRateCard(
        catalog_id="test-catalog",
        catalog_digest="sha256:test-catalog",
        schedule_version="test-v1",
        effective_tier="fixed",
        uncached_input_usd_nanos_per_million=1_000_000_000,
        cached_input_usd_nanos_per_million=500_000_000,
        output_usd_nanos_per_million=2_000_000_000,
    )


def _context(call_id: str, input_upper: int = 200, output_upper: int = 50) -> ModelCallContext:
    return ModelCallContext(
        call_id=call_id,
        request_digest=f"sha256:{call_id}",
        requested_model="test/model",
        input_token_upper=input_upper,
        output_token_upper=output_upper,
    )


def _usage(
    *,
    prompt: int = 100,
    cached: int = 25,
    completion: int = 10,
    provider_cost_usd_nanos: int | None = None,
    metadata_receipt: bytes = b"",
) -> ProviderUsage:
    return ProviderUsage(
        requested_model="test/model",
        returned_model="test/model-20260821",
        metadata_model=None,
        provider_name="Test Provider",
        provider_request_id="provider-request-1",
        provider_generation_id=None,
        provider_timestamp="2026-08-21T10:00:00Z",
        system_fingerprint="test-fingerprint",
        provider_cost_usd_nanos=provider_cost_usd_nanos,
        prompt_tokens=prompt,
        cached_input_tokens=cached,
        completion_tokens=completion,
        raw_receipt=b'{"usage":"retained-verbatim"}',
        raw_metadata_receipt=metadata_receipt,
    )


class SqliteBudgetAccountTests(unittest.TestCase):
    def test_close_reconciliation_rejects_tampered_receipts_and_accounting(self) -> None:
        mutations: tuple[tuple[str, str, tuple[object, ...]], ...] = (
            (
                "raw stream",
                "UPDATE budget_reservations SET raw_receipt = ?",
                (b'{"usage":"tampered"}',),
            ),
            (
                "raw metadata",
                "UPDATE budget_reservations SET raw_metadata_receipt = ?",
                (b'{"data":{"id":"tampered"}}',),
            ),
            (
                "usage digest",
                "UPDATE budget_reservations SET usage_digest = ?",
                ("sha256:tampered",),
            ),
            (
                "charged cost",
                "UPDATE budget_reservations SET charged_usd_nanos = ?",
                (322,),
            ),
            (
                "actor total",
                "UPDATE budget_actors SET charged_usd_nanos = ?",
                (320,),
            ),
            (
                "campaign total",
                "UPDATE budget_campaigns SET charged_usd_nanos = ?",
                (320,),
            ),
            (
                "limits",
                "UPDATE budget_actors SET limit_usd_nanos = 320; "
                "UPDATE budget_campaigns SET limit_usd_nanos = 320",
                (),
            ),
        )
        for label, statement, parameters in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "budget.sqlite3"
                account = self._settled_account(database)
                with sqlite3.connect(database) as connection:
                    if ";" in statement:
                        connection.executescript(statement)
                    else:
                        connection.execute(statement, parameters)
                    connection.commit()

                reconciliation = account.reconcile("tamper-run")

                self.assertFalse(reconciliation.valid)
                self.assertTrue(reconciliation.ledger_errors)

    def test_close_reconciliation_rejects_tampered_usage_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "budget.sqlite3"
            account = self._settled_account(database)
            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    "SELECT usage_json FROM budget_reservations"
                ).fetchone()
                assert row is not None
                payload = json.loads(str(row[0]))
                payload["completion_tokens"] = 3
                connection.execute(
                    "UPDATE budget_reservations SET usage_json = ?",
                    (json.dumps(payload),),
                )
                connection.commit()

            reconciliation = account.reconcile("tamper-run")

            self.assertFalse(reconciliation.valid)
            self.assertTrue(
                any(
                    error.startswith("receipt_or_usage_invalid:")
                    for error in reconciliation.ledger_errors
                )
            )

    def test_close_reconciliation_rejects_tampered_terminal_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "budget.sqlite3"
            account = self._settled_account(database)
            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    "SELECT sequence, details_json FROM budget_audit "
                    "WHERE kind = 'reservation.settled'"
                ).fetchone()
                assert row is not None
                details = json.loads(str(row[1]))
                details["receipt_digest"] = "sha256:tampered"
                connection.execute(
                    "UPDATE budget_audit SET details_json = ? WHERE sequence = ?",
                    (json.dumps(details), int(row[0])),
                )
                connection.commit()

            reconciliation = account.reconcile("tamper-run")

            self.assertFalse(reconciliation.valid)
            self.assertTrue(
                any(
                    error.startswith("terminal_audit_evidence_mismatch:")
                    for error in reconciliation.ledger_errors
                )
            )

    @staticmethod
    def _settled_account(database: Path) -> SqliteBudgetAccount:
        account = SqliteBudgetAccount(database, _rate_card())
        account.open_campaign(
            "tamper-run",
            100_000,
            (ActorBudgetAllocation("tamper-run", "actor-0", 100_000),),
        )
        reservation = account.reserve(
            "tamper-run", "actor-0", _context("settled", 10, 10)
        )
        assert isinstance(reservation, BudgetReservation)
        account.settle(
            reservation.reservation_id,
            _usage(
                prompt=10,
                cached=0,
                completion=2,
                provider_cost_usd_nanos=321,
                metadata_receipt=b'{"data":{"id":"complete"}}',
            ),
        )
        return account

    def test_close_reconciliation_accepts_complete_receipts_and_releases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            account = SqliteBudgetAccount(
                Path(directory) / "budget.sqlite3", _rate_card()
            )
            account.open_campaign(
                "valid-close",
                100_000,
                (ActorBudgetAllocation("valid-close", "actor-0", 100_000),),
            )
            settled = account.reserve(
                "valid-close", "actor-0", _context("settled", 10, 10)
            )
            released = account.reserve(
                "valid-close", "actor-0", _context("released", 10, 10)
            )
            assert isinstance(settled, BudgetReservation)
            assert isinstance(released, BudgetReservation)
            account.settle(
                settled.reservation_id,
                _usage(
                    prompt=10,
                    cached=0,
                    completion=2,
                    provider_cost_usd_nanos=321,
                    metadata_receipt=b'{"data":{"id":"complete"}}',
                ),
            )
            account.release(released.reservation_id, "definitely unstarted")

            reconciliation = account.reconcile("valid-close")

            self.assertTrue(reconciliation.valid)
            self.assertEqual(
                reconciliation.accounting_mode, "provider_receipts_required"
            )

    def test_close_reconciliation_reports_every_invalid_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            account = SqliteBudgetAccount(
                Path(directory) / "budget.sqlite3", _rate_card()
            )
            account.open_campaign(
                "invalid-close",
                1_000_000,
                (
                    ActorBudgetAllocation(
                        "invalid-close", "actor-0", 1_000_000
                    ),
                ),
            )
            active = account.reserve(
                "invalid-close", "actor-0", _context("active", 10, 10)
            )
            forfeited = account.reserve(
                "invalid-close", "actor-0", _context("forfeited", 10, 10)
            )
            overrun = account.reserve(
                "invalid-close", "actor-0", _context("overrun-close", 1, 1)
            )
            missing = account.reserve(
                "invalid-close", "actor-0", _context("missing", 10, 10)
            )
            assert isinstance(active, BudgetReservation)
            assert isinstance(forfeited, BudgetReservation)
            assert isinstance(overrun, BudgetReservation)
            assert isinstance(missing, BudgetReservation)
            account.forfeit(
                forfeited.reservation_id,
                "invalid provider identity",
                b'{"error":"identity"}',
            )
            with self.assertRaisesRegex(RuntimeError, "exceeded"):
                account.settle(
                    overrun.reservation_id,
                    _usage(
                        prompt=1,
                        cached=0,
                        completion=1,
                        provider_cost_usd_nanos=4_000,
                        metadata_receipt=b'{"data":{"id":"overrun"}}',
                    ),
                )
            account.settle(
                missing.reservation_id,
                _usage(
                    prompt=10,
                    cached=0,
                    completion=2,
                    provider_cost_usd_nanos=321,
                ),
            )

            reconciliation = account.reconcile("invalid-close")

            self.assertFalse(reconciliation.valid)
            self.assertEqual(
                reconciliation.active_reservation_ids,
                (active.reservation_id,),
            )
            self.assertEqual(
                reconciliation.forfeited_reservation_ids,
                (forfeited.reservation_id,),
            )
            self.assertEqual(
                reconciliation.overrun_reservation_ids,
                (overrun.reservation_id,),
            )
            self.assertEqual(
                reconciliation.missing_receipt_reservation_ids,
                (missing.reservation_id,),
            )
            self.assertEqual(reconciliation.ledger_errors, ())

    def test_provider_billed_cost_and_metadata_receipt_are_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "budget.sqlite3"
            account = SqliteBudgetAccount(database, _rate_card())
            account.open_campaign(
                "receipt-run",
                100_000,
                (ActorBudgetAllocation("receipt-run", "actor-0", 100_000),),
            )
            reservation = account.reserve(
                "receipt-run", "actor-0", _context("provider-cost", 10, 10)
            )
            self.assertIsInstance(reservation, BudgetReservation)
            assert isinstance(reservation, BudgetReservation)
            usage = _usage(
                prompt=10,
                cached=0,
                completion=2,
                provider_cost_usd_nanos=321,
                metadata_receipt=b'{"data":{"total_cost":0.000000321}}',
            )

            charge = account.settle(reservation.reservation_id, usage)
            reopened = SqliteBudgetAccount(database, _rate_card())
            restored = reopened.snapshot("receipt-run").charges[0]

            self.assertEqual(charge.charged_usd_nanos, 321)
            self.assertEqual(restored.charged_usd_nanos, 321)
            self.assertEqual(restored.usage.provider_cost_usd_nanos, 321)
            self.assertEqual(
                restored.usage.raw_metadata_receipt,
                usage.raw_metadata_receipt,
            )
            self.assertEqual(
                restored.usage.metadata_receipt_digest,
                usage.metadata_receipt_digest,
            )

    def test_actor_reservations_settlement_rejection_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "budget.sqlite3"
            account = SqliteBudgetAccount(database, _rate_card())
            allocations = (
                ActorBudgetAllocation("budget-run", "actor-0", 500_000),
                ActorBudgetAllocation("budget-run", "actor-1", 500_000),
            )
            account.open_campaign("budget-run", 1_000_000, allocations)
            first = account.reserve("budget-run", "actor-0", _context("first"))
            self.assertIsInstance(first, BudgetReservation)
            assert isinstance(first, BudgetReservation)
            rejected = account.reserve(
                "budget-run", "actor-0", _context("too-large", 300, 100)
            )
            self.assertIsInstance(rejected, BudgetRejected)
            assert isinstance(rejected, BudgetRejected)
            self.assertEqual(rejected.reason, "actor_budget_exhausted")

            independent = account.reserve(
                "budget-run", "actor-1", _context("independent")
            )
            self.assertIsInstance(independent, BudgetReservation)
            assert isinstance(independent, BudgetReservation)
            charge = account.settle(first.reservation_id, _usage())
            self.assertEqual(charge.charged_usd_nanos, 107_500)
            account.release(independent.reservation_id, "test completed upstream-free")

            restarted = SqliteBudgetAccount(database, _rate_card())
            restarted.open_campaign("budget-run", 1_000_000, allocations)
            snapshot = restarted.snapshot("budget-run")
            self.assertEqual(snapshot.organisation_reserved_usd_nanos, 0)
            self.assertEqual(snapshot.organisation_charged_usd_nanos, 107_500)
            self.assertEqual(snapshot.actors[0].charged_usd_nanos, 107_500)
            self.assertEqual(snapshot.actors[1].charged_usd_nanos, 0)
            self.assertEqual(len(snapshot.charges), 1)
            self.assertEqual(
                snapshot.charges[0].usage.receipt_digest,
                _usage().receipt_digest,
            )
            self.assertEqual(
                [event["kind"] for event in snapshot.audit_events],
                [
                    "campaign.opened",
                    "reservation.created",
                    "reservation.rejected",
                    "reservation.created",
                    "reservation.settled",
                    "reservation.released",
                ],
            )

    def test_concurrent_admission_cannot_oversubscribe_an_actor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            account = SqliteBudgetAccount(
                Path(directory) / "budget.sqlite3", _rate_card()
            )
            account.open_campaign(
                "concurrent-run",
                1_000_000,
                (ActorBudgetAllocation("concurrent-run", "actor-0", 1_000_000),),
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = tuple(
                    executor.map(
                        lambda call_id: account.reserve(
                            "concurrent-run",
                            "actor-0",
                            _context(call_id, 400, 100),
                        ),
                        ("concurrent-1", "concurrent-2"),
                    )
                )
            self.assertEqual(
                sum(isinstance(result, BudgetReservation) for result in results), 1
            )
            self.assertEqual(
                sum(isinstance(result, BudgetRejected) for result in results), 1
            )
            snapshot = account.snapshot("concurrent-run")
            self.assertEqual(snapshot.organisation_reserved_usd_nanos, 600_000)
            self.assertGreaterEqual(snapshot.organisation_remaining_usd_nanos, 0)

    def test_missing_usage_forfeits_and_overrun_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            account = SqliteBudgetAccount(
                Path(directory) / "budget.sqlite3", _rate_card()
            )
            account.open_campaign(
                "failure-run",
                2_000_000,
                (ActorBudgetAllocation("failure-run", "actor-0", 2_000_000),),
            )
            missing = account.reserve(
                "failure-run", "actor-0", _context("missing", 100, 50)
            )
            assert isinstance(missing, BudgetReservation)
            account.forfeit(
                missing.reservation_id,
                "provider receipt missing",
                b"incomplete upstream response",
            )

            overrun = account.reserve(
                "failure-run", "actor-0", _context("overrun", 1, 1)
            )
            assert isinstance(overrun, BudgetReservation)
            with self.assertRaisesRegex(RuntimeError, "exceeded"):
                account.settle(
                    overrun.reservation_id,
                    _usage(prompt=100, cached=0, completion=100),
                )
            snapshot = account.snapshot("failure-run")
            self.assertEqual(len(snapshot.charges), 1)
            self.assertEqual(snapshot.organisation_reserved_usd_nanos, 0)
            self.assertIn(
                "reservation.forfeited",
                [event["kind"] for event in snapshot.audit_events],
            )
            self.assertIn(
                "reservation.overrun",
                [event["kind"] for event in snapshot.audit_events],
            )

    def test_campaign_allocations_must_partition_the_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            account = SqliteBudgetAccount(
                Path(directory) / "budget.sqlite3", _rate_card()
            )
            with self.assertRaisesRegex(ValueError, "partition"):
                account.open_campaign(
                    "bad-run",
                    1_000_000,
                    (ActorBudgetAllocation("bad-run", "actor-0", 900_000),),
                )


if __name__ == "__main__":
    unittest.main()
