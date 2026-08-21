"""Durable fixed-point budget accounting for model gateway calls."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from ..budget import (
    ActorBudgetAllocation,
    ActorBudgetSnapshot,
    BillingRateCard,
    BudgetCharge,
    BudgetRejected,
    BudgetReservation,
    BudgetSnapshot,
    ModelCallContext,
    ProviderUsage,
)
from ..canonical import canonical_json_bytes, digest_bytes, digest_value, parse_json


class SqliteBudgetAccount:
    """Enforce organisation and actor model budgets with durable reservations."""

    def __init__(self, database: Path, rate_card: BillingRateCard) -> None:
        self._database = database
        self._rate_card = rate_card
        self._rate_card_digest = digest_value(rate_card)
        self._lock = threading.RLock()
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS budget_campaigns (
                    campaign_run_id TEXT PRIMARY KEY,
                    limit_usd_nanos INTEGER NOT NULL,
                    reserved_usd_nanos INTEGER NOT NULL DEFAULT 0,
                    charged_usd_nanos INTEGER NOT NULL DEFAULT 0,
                    rate_card_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS budget_actors (
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    limit_usd_nanos INTEGER NOT NULL,
                    reserved_usd_nanos INTEGER NOT NULL DEFAULT 0,
                    charged_usd_nanos INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (campaign_run_id, actor_id),
                    FOREIGN KEY (campaign_run_id)
                        REFERENCES budget_campaigns(campaign_run_id)
                );
                CREATE TABLE IF NOT EXISTS budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    requested_model TEXT NOT NULL,
                    maximum_usd_nanos INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    charged_usd_nanos INTEGER,
                    usage_digest TEXT,
                    usage_json TEXT,
                    raw_receipt BLOB,
                    raw_metadata_receipt BLOB,
                    UNIQUE (campaign_run_id, actor_id, call_id),
                    FOREIGN KEY (campaign_run_id, actor_id)
                        REFERENCES budget_actors(campaign_run_id, actor_id)
                );
                CREATE TABLE IF NOT EXISTS budget_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_run_id TEXT NOT NULL,
                    actor_id TEXT,
                    kind TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )
            reservation_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(budget_reservations)"
                ).fetchall()
            }
            if "raw_metadata_receipt" not in reservation_columns:
                connection.execute(
                    "ALTER TABLE budget_reservations "
                    "ADD COLUMN raw_metadata_receipt BLOB"
                )
            connection.commit()

    @property
    def rate_card_digest(self) -> str:
        return self._rate_card_digest

    def open_campaign(
        self,
        campaign_run_id: str,
        organisation_limit_usd_nanos: int,
        allocations: tuple[ActorBudgetAllocation, ...],
    ) -> None:
        if not campaign_run_id:
            raise ValueError("campaign identifier must be nonempty")
        if type(organisation_limit_usd_nanos) is not int or organisation_limit_usd_nanos < 1:
            raise ValueError("organisation budget limit must be a positive integer")
        if not allocations:
            raise ValueError("at least one actor allocation is required")
        actor_ids = [allocation.actor_id for allocation in allocations]
        if len(set(actor_ids)) != len(actor_ids):
            raise ValueError("actor budget allocations must be unique")
        if any(allocation.campaign_run_id != campaign_run_id for allocation in allocations):
            raise ValueError("actor allocation belongs to another campaign")
        allocated = sum(
            allocation.limit_usd_nanos for allocation in allocations
        )
        if allocated != organisation_limit_usd_nanos:
            raise ValueError("actor allocations must partition the organisation budget exactly")

        with self._transaction() as connection:
            campaign = connection.execute(
                "SELECT * FROM budget_campaigns WHERE campaign_run_id = ?",
                (campaign_run_id,),
            ).fetchone()
            existing_actors = connection.execute(
                "SELECT actor_id, limit_usd_nanos FROM budget_actors "
                "WHERE campaign_run_id = ? ORDER BY actor_id",
                (campaign_run_id,),
            ).fetchall()
            expected_actors = sorted(
                (allocation.actor_id, allocation.limit_usd_nanos)
                for allocation in allocations
            )
            if campaign is not None:
                actual_actors = [
                    (str(row["actor_id"]), int(row["limit_usd_nanos"]))
                    for row in existing_actors
                ]
                if (
                    int(campaign["limit_usd_nanos"])
                    != organisation_limit_usd_nanos
                    or str(campaign["rate_card_digest"])
                    != self._rate_card_digest
                    or actual_actors != expected_actors
                ):
                    raise ValueError("budget campaign was already opened differently")
                return
            connection.execute(
                "INSERT INTO budget_campaigns "
                "(campaign_run_id, limit_usd_nanos, rate_card_digest) "
                "VALUES (?, ?, ?)",
                (
                    campaign_run_id,
                    organisation_limit_usd_nanos,
                    self._rate_card_digest,
                ),
            )
            connection.executemany(
                "INSERT INTO budget_actors "
                "(campaign_run_id, actor_id, limit_usd_nanos) VALUES (?, ?, ?)",
                [
                    (
                        campaign_run_id,
                        allocation.actor_id,
                        allocation.limit_usd_nanos,
                    )
                    for allocation in allocations
                ],
            )
            self._audit(
                connection,
                campaign_run_id,
                None,
                "campaign.opened",
                {
                    "organisation_limit_usd_nanos": organisation_limit_usd_nanos,
                    "actors": [
                        {"actor_id": actor_id, "limit_usd_nanos": limit}
                        for actor_id, limit in expected_actors
                    ],
                    "rate_card_digest": self._rate_card_digest,
                },
            )

    def reserve(
        self,
        campaign_run_id: str,
        actor_id: str,
        context: ModelCallContext,
    ) -> BudgetReservation | BudgetRejected:
        maximum = self._rate_card.reserve_maximum(
            context.input_token_upper, context.output_token_upper
        )
        reservation_digest = digest_value(
            {
                "campaign": campaign_run_id,
                "actor": actor_id,
                "call": context.call_id,
            }
        )
        reservation_id = f"reservation-{reservation_digest[7:39]}"
        with self._transaction() as connection:
            campaign = connection.execute(
                "SELECT * FROM budget_campaigns WHERE campaign_run_id = ?",
                (campaign_run_id,),
            ).fetchone()
            actor = connection.execute(
                "SELECT * FROM budget_actors WHERE campaign_run_id = ? AND actor_id = ?",
                (campaign_run_id, actor_id),
            ).fetchone()
            if campaign is None or actor is None:
                raise PermissionError("budget actor is not provisioned")
            self._require_rate_card(campaign)
            existing = connection.execute(
                "SELECT * FROM budget_reservations WHERE campaign_run_id = ? "
                "AND actor_id = ? AND call_id = ?",
                (campaign_run_id, actor_id, context.call_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["request_digest"]) != context.request_digest
                    or str(existing["requested_model"]) != context.requested_model
                    or int(existing["maximum_usd_nanos"]) != maximum
                ):
                    raise ValueError("model call identifier was reused differently")
                if str(existing["status"]) != "reserved":
                    raise ValueError("model call reservation is no longer active")
                return self._reservation(existing)
            actor_remaining = self._remaining(actor)
            organisation_remaining = self._remaining(campaign)
            if maximum > actor_remaining or maximum > organisation_remaining:
                reason = (
                    "actor_budget_exhausted"
                    if maximum > actor_remaining
                    else "organisation_budget_exhausted"
                )
                rejection = BudgetRejected(
                    campaign_run_id,
                    actor_id,
                    context.call_id,
                    reason,
                    maximum,
                    actor_remaining,
                    organisation_remaining,
                )
                self._audit(
                    connection,
                    campaign_run_id,
                    actor_id,
                    "reservation.rejected",
                    {
                        "call_id": context.call_id,
                        "reason": reason,
                        "required_usd_nanos": maximum,
                        "actor_remaining_usd_nanos": actor_remaining,
                        "organisation_remaining_usd_nanos": organisation_remaining,
                    },
                )
                return rejection

            connection.execute(
                "INSERT INTO budget_reservations "
                "(reservation_id, campaign_run_id, actor_id, call_id, "
                "request_digest, requested_model, maximum_usd_nanos, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved')",
                (
                    reservation_id,
                    campaign_run_id,
                    actor_id,
                    context.call_id,
                    context.request_digest,
                    context.requested_model,
                    maximum,
                ),
            )
            for table in ("budget_campaigns", "budget_actors"):
                where = "campaign_run_id = ?"
                values: tuple[object, ...] = (maximum, campaign_run_id)
                if table == "budget_actors":
                    where += " AND actor_id = ?"
                    values = (*values, actor_id)
                connection.execute(
                    f"UPDATE {table} SET reserved_usd_nanos = "  # noqa: S608
                    f"reserved_usd_nanos + ? WHERE {where}",
                    values,
                )
            self._audit(
                connection,
                campaign_run_id,
                actor_id,
                "reservation.created",
                {
                    "reservation_id": reservation_id,
                    "call_id": context.call_id,
                    "request_digest": context.request_digest,
                    "maximum_usd_nanos": maximum,
                },
            )
            return BudgetReservation(
                reservation_id,
                campaign_run_id,
                actor_id,
                context.call_id,
                maximum,
            )

    def has_actor(self, campaign_run_id: str, actor_id: str) -> bool:
        with closing(self._connect()) as connection:
            campaign = connection.execute(
                "SELECT * FROM budget_campaigns WHERE campaign_run_id = ?",
                (campaign_run_id,),
            ).fetchone()
            if campaign is None:
                return False
            self._require_rate_card(campaign)
            row = connection.execute(
                "SELECT 1 FROM budget_actors WHERE campaign_run_id = ? "
                "AND actor_id = ?",
                (campaign_run_id, actor_id),
            ).fetchone()
            return row is not None

    def settle(
        self, reservation_id: str, usage: ProviderUsage
    ) -> BudgetCharge:
        charged = self._rate_card.charge(usage)
        overrun = False
        with self._transaction() as connection:
            row = self._active_reservation(connection, reservation_id)
            if str(row["requested_model"]) != usage.requested_model:
                raise ValueError("provider usage requested model differs from reservation")
            maximum = int(row["maximum_usd_nanos"])
            campaign_run_id = str(row["campaign_run_id"])
            campaign = connection.execute(
                "SELECT * FROM budget_campaigns WHERE campaign_run_id = ?",
                (campaign_run_id,),
            ).fetchone()
            assert campaign is not None
            self._require_rate_card(campaign)
            actor_id = str(row["actor_id"])
            call_id = str(row["call_id"])
            overrun = charged > maximum
            status = "overrun" if overrun else "settled"
            usage_payload = self._usage_payload(usage)
            connection.execute(
                "UPDATE budget_reservations SET status = ?, charged_usd_nanos = ?, "
                "usage_digest = ?, usage_json = ?, raw_receipt = ?, "
                "raw_metadata_receipt = ? "
                "WHERE reservation_id = ?",
                (
                    status,
                    charged,
                    usage.usage_digest,
                    canonical_json_bytes(usage_payload).decode("utf-8"),
                    usage.raw_receipt,
                    usage.raw_metadata_receipt or None,
                    reservation_id,
                ),
            )
            self._move_reservation_to_charge(
                connection, campaign_run_id, actor_id, maximum, charged
            )
            self._audit(
                connection,
                campaign_run_id,
                actor_id,
                f"reservation.{status}",
                {
                    "reservation_id": reservation_id,
                    "call_id": call_id,
                    "maximum_usd_nanos": maximum,
                    "charged_usd_nanos": charged,
                    "usage_digest": usage.usage_digest,
                    "receipt_digest": usage.receipt_digest,
                    "metadata_receipt_digest": usage.metadata_receipt_digest,
                    "rate_card_digest": self._rate_card_digest,
                    "requested_model": usage.requested_model,
                    "returned_model": usage.returned_model,
                    "metadata_model": usage.metadata_model,
                    "provider_name": usage.provider_name,
                    "provider_request_id": usage.provider_request_id,
                    "provider_generation_id": usage.provider_generation_id,
                    "provider_timestamp": usage.provider_timestamp,
                    "system_fingerprint": usage.system_fingerprint,
                    "provider_cost_usd_nanos": usage.provider_cost_usd_nanos,
                    "prompt_tokens": usage.prompt_tokens,
                    "cached_input_tokens": usage.cached_input_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "effective_tier": self._rate_card.effective_tier,
                    "uncached_input_usd_nanos_per_million": (
                        self._rate_card.uncached_input_usd_nanos_per_million
                    ),
                    "cached_input_usd_nanos_per_million": (
                        self._rate_card.cached_input_usd_nanos_per_million
                    ),
                    "output_usd_nanos_per_million": (
                        self._rate_card.output_usd_nanos_per_million
                    ),
                },
            )
        charge = BudgetCharge(
            reservation_id,
            campaign_run_id,
            actor_id,
            call_id,
            charged,
            usage,
            self._rate_card_digest,
        )
        if overrun:
            raise RuntimeError("provider charge exceeded its conservative reservation")
        return charge

    def forfeit(self, reservation_id: str, reason: str, raw_receipt: bytes) -> None:
        """Consume a full reservation when provider usage is missing or ambiguous."""

        if not reason or not raw_receipt:
            raise ValueError("forfeit reason and raw receipt must be retained")
        with self._transaction() as connection:
            row = self._active_reservation(connection, reservation_id)
            maximum = int(row["maximum_usd_nanos"])
            campaign_run_id = str(row["campaign_run_id"])
            actor_id = str(row["actor_id"])
            connection.execute(
                "UPDATE budget_reservations SET status = 'forfeited', "
                "charged_usd_nanos = ?, raw_receipt = ? WHERE reservation_id = ?",
                (maximum, raw_receipt, reservation_id),
            )
            self._move_reservation_to_charge(
                connection, campaign_run_id, actor_id, maximum, maximum
            )
            self._audit(
                connection,
                campaign_run_id,
                actor_id,
                "reservation.forfeited",
                {
                    "reservation_id": reservation_id,
                    "reason": reason,
                    "charged_usd_nanos": maximum,
                    "receipt_digest": digest_bytes(raw_receipt),
                },
            )

    def release(self, reservation_id: str, reason: str) -> None:
        if not reason:
            raise ValueError("reservation release reason must be nonempty")
        with self._transaction() as connection:
            row = self._active_reservation(connection, reservation_id)
            maximum = int(row["maximum_usd_nanos"])
            campaign_run_id = str(row["campaign_run_id"])
            actor_id = str(row["actor_id"])
            connection.execute(
                "UPDATE budget_reservations SET status = 'released' "
                "WHERE reservation_id = ?",
                (reservation_id,),
            )
            self._move_reservation_to_charge(
                connection, campaign_run_id, actor_id, maximum, 0
            )
            self._audit(
                connection,
                campaign_run_id,
                actor_id,
                "reservation.released",
                {"reservation_id": reservation_id, "reason": reason},
            )

    def snapshot(self, campaign_run_id: str) -> BudgetSnapshot:
        with closing(self._connect()) as connection:
            campaign = connection.execute(
                "SELECT * FROM budget_campaigns WHERE campaign_run_id = ?",
                (campaign_run_id,),
            ).fetchone()
            if campaign is None:
                raise KeyError("budget campaign is not provisioned")
            self._require_rate_card(campaign)
            actors = connection.execute(
                "SELECT * FROM budget_actors WHERE campaign_run_id = ? ORDER BY actor_id",
                (campaign_run_id,),
            ).fetchall()
            charge_rows = connection.execute(
                "SELECT * FROM budget_reservations WHERE campaign_run_id = ? "
                "AND usage_json IS NOT NULL ORDER BY call_id",
                (campaign_run_id,),
            ).fetchall()
            audit = connection.execute(
                "SELECT sequence, actor_id, kind, details_json FROM budget_audit "
                "WHERE campaign_run_id = ? ORDER BY sequence",
                (campaign_run_id,),
            ).fetchall()
        return BudgetSnapshot(
            campaign_run_id,
            int(campaign["limit_usd_nanos"]),
            int(campaign["reserved_usd_nanos"]),
            int(campaign["charged_usd_nanos"]),
            tuple(
                ActorBudgetSnapshot(
                    str(row["actor_id"]),
                    int(row["limit_usd_nanos"]),
                    int(row["reserved_usd_nanos"]),
                    int(row["charged_usd_nanos"]),
                )
                for row in actors
            ),
            tuple(self._charge(row) for row in charge_rows),
            tuple(
                {
                    "sequence": int(row["sequence"]),
                    "actor_id": row["actor_id"],
                    "kind": str(row["kind"]),
                    "details": parse_json(str(row["details_json"])),
                }
                for row in audit
            ),
        )

    def _charge(self, row: sqlite3.Row) -> BudgetCharge:
        payload = parse_json(str(row["usage_json"]))
        if not isinstance(payload, dict):
            raise RuntimeError("stored provider usage is invalid")
        raw_receipt = row["raw_receipt"]
        if not isinstance(raw_receipt, bytes):
            raise RuntimeError("stored provider receipt is invalid")
        usage = ProviderUsage(
            requested_model=str(payload["requested_model"]),
            returned_model=self._nullable_string(payload["returned_model"]),
            metadata_model=self._nullable_string(payload["metadata_model"]),
            provider_name=self._nullable_string(payload["provider_name"]),
            provider_request_id=self._nullable_string(
                payload["provider_request_id"]
            ),
            provider_generation_id=self._nullable_string(
                payload["provider_generation_id"]
            ),
            provider_timestamp=self._nullable_string(
                payload["provider_timestamp"]
            ),
            system_fingerprint=self._nullable_string(
                payload["system_fingerprint"]
            ),
            provider_cost_usd_nanos=self._nullable_integer(
                payload["provider_cost_usd_nanos"]
            ),
            prompt_tokens=int(payload["prompt_tokens"]),
            cached_input_tokens=int(payload["cached_input_tokens"]),
            completion_tokens=int(payload["completion_tokens"]),
            raw_receipt=raw_receipt,
            raw_metadata_receipt=(
                bytes(row["raw_metadata_receipt"])
                if row["raw_metadata_receipt"] is not None
                else b""
            ),
        )
        if usage.usage_digest != str(row["usage_digest"]):
            raise RuntimeError("stored provider usage digest differs")
        return BudgetCharge(
            str(row["reservation_id"]),
            str(row["campaign_run_id"]),
            str(row["actor_id"]),
            str(row["call_id"]),
            int(row["charged_usd_nanos"]),
            usage,
            self._rate_card_digest,
        )

    @staticmethod
    def _nullable_string(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise RuntimeError("stored provider usage string is invalid")
        return value

    @staticmethod
    def _nullable_integer(value: object) -> int | None:
        if value is None:
            return None
        if type(value) is not int:
            raise RuntimeError("stored provider usage integer is invalid")
        return value

    @staticmethod
    def _remaining(row: sqlite3.Row) -> int:
        return (
            int(row["limit_usd_nanos"])
            - int(row["reserved_usd_nanos"])
            - int(row["charged_usd_nanos"])
        )

    def _require_rate_card(self, campaign: sqlite3.Row) -> None:
        if str(campaign["rate_card_digest"]) != self._rate_card_digest:
            raise ValueError("budget campaign uses a different rate card")

    @staticmethod
    def _reservation(row: sqlite3.Row) -> BudgetReservation:
        return BudgetReservation(
            str(row["reservation_id"]),
            str(row["campaign_run_id"]),
            str(row["actor_id"]),
            str(row["call_id"]),
            int(row["maximum_usd_nanos"]),
        )

    @staticmethod
    def _usage_payload(usage: ProviderUsage) -> dict[str, object]:
        return {
            "requested_model": usage.requested_model,
            "returned_model": usage.returned_model,
            "metadata_model": usage.metadata_model,
            "provider_name": usage.provider_name,
            "provider_request_id": usage.provider_request_id,
            "provider_generation_id": usage.provider_generation_id,
            "provider_timestamp": usage.provider_timestamp,
            "system_fingerprint": usage.system_fingerprint,
            "provider_cost_usd_nanos": usage.provider_cost_usd_nanos,
            "prompt_tokens": usage.prompt_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "completion_tokens": usage.completion_tokens,
            "receipt_digest": usage.receipt_digest,
            "metadata_receipt_digest": usage.metadata_receipt_digest,
        }

    @staticmethod
    def _active_reservation(
        connection: sqlite3.Connection, reservation_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM budget_reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise KeyError("budget reservation does not exist")
        if str(row["status"]) != "reserved":
            raise ValueError("budget reservation is no longer active")
        return row

    @staticmethod
    def _move_reservation_to_charge(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        actor_id: str,
        reserved: int,
        charged: int,
    ) -> None:
        connection.execute(
            "UPDATE budget_campaigns SET reserved_usd_nanos = "
            "reserved_usd_nanos - ?, charged_usd_nanos = charged_usd_nanos + ? "
            "WHERE campaign_run_id = ?",
            (reserved, charged, campaign_run_id),
        )
        connection.execute(
            "UPDATE budget_actors SET reserved_usd_nanos = "
            "reserved_usd_nanos - ?, charged_usd_nanos = charged_usd_nanos + ? "
            "WHERE campaign_run_id = ? AND actor_id = ?",
            (reserved, charged, campaign_run_id, actor_id),
        )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        campaign_run_id: str,
        actor_id: str | None,
        kind: str,
        details: dict[str, object],
    ) -> None:
        connection.execute(
            "INSERT INTO budget_audit "
            "(campaign_run_id, actor_id, kind, details_json) VALUES (?, ?, ?, ?)",
            (
                campaign_run_id,
                actor_id,
                kind,
                canonical_json_bytes(details).decode("utf-8"),
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    class _Transaction:
        def __init__(self, account: "SqliteBudgetAccount") -> None:
            self._account = account
            self._connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._account._lock.acquire()
            self._connection = self._account._connect()
            self._connection.execute("BEGIN IMMEDIATE")
            return self._connection

        def __exit__(self, error_type: object, error: object, traceback: object) -> None:
            assert self._connection is not None
            try:
                if error_type is None:
                    self._connection.commit()
                else:
                    self._connection.rollback()
            finally:
                self._connection.close()
                self._account._lock.release()

    def _transaction(self) -> "SqliteBudgetAccount._Transaction":
        return self._Transaction(self)
