"""Explicit close gate for local harness tests that make no model calls."""

from __future__ import annotations

from ..budget import BudgetReconciliation


class NoModelBudgetReconciler:
    """Attest that a development-only campaign has no model budget ledger."""

    def reconcile(self, campaign_run_id: str) -> BudgetReconciliation:
        return BudgetReconciliation(
            campaign_run_id=campaign_run_id,
            accounting_mode="no_model_calls",
        )
