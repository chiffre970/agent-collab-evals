from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_collab_evals.adapters.fake_serving_evaluator import (
    FakeModelServingEvaluator,
)
from agent_collab_evals.adapters.split_scope_evaluator import (
    EvaluationLaneProfile,
    RegisteredEvaluationProfile,
    SplitScopeServingEvaluator,
)
from agent_collab_evals.artifacts import ArtifactRef
from agent_collab_evals.campaigns.model_serving import ModelServingCampaign
from agent_collab_evals.canonical import digest_value
from agent_collab_evals.evaluation import (
    EvaluationReservation,
    EvaluationReservationStatus,
    EvaluationScope,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class SplitScopeServingEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.campaign = ModelServingCampaign.load(
            REPOSITORY_ROOT / "campaigns/model_serving_v0/campaign.toml"
        )
        scores = {"stock-vllm-0.21.0": 1_000_000}
        self.visible = FakeModelServingEvaluator(
            self.root / "visible.sqlite3",
            self.campaign,
            scores,
            {},
            visible_used_seconds=7,
        )
        self.hidden = FakeModelServingEvaluator(
            self.root / "hidden.sqlite3",
            self.campaign,
            {},
            {"stock-vllm-0.21.0": 930_000},
            hidden_used_seconds=11,
        )
        self.visible_lane = self._lane(
            EvaluationScope.VISIBLE,
            self.visible.profile_digest,
            "actor-evaluation-account",
            "public-evidence",
            "public-workload",
        )
        self.hidden_lane = self._lane(
            EvaluationScope.HIDDEN,
            self.hidden.profile_digest,
            "hidden-evaluator-account",
            "hidden-evidence",
            "hidden-workload",
        )
        self.profile = RegisteredEvaluationProfile(
            profile_id="model-serving-registered-v1",
            campaign_manifest_digest=self.campaign.manifest_digest,
            registration_manifest_digest=digest_value({"study": "registered-v1"}),
            visible=self.visible_lane,
            hidden=self.hidden_lane,
        )
        self.evaluator = SplitScopeServingEvaluator(
            self.root / "split.sqlite3",
            self.profile,
            self.visible,
            self.hidden,
        )
        self.candidate = self.campaign.reference_candidate_path.read_bytes()
        artifact = ArtifactRef("artifact-" + "8" * 32)
        self.visible_reservation = EvaluationReservation(
            reservation_id="evaluation-" + "7" * 32,
            reservation_key="visible:test",
            campaign_run_id="campaign-run",
            actor_id="actor-0",
            artifact_ref=artifact,
            scope=EvaluationScope.VISIBLE,
            reserved_seconds=60,
            status=EvaluationReservationStatus.RESERVED,
        )
        self.hidden_reservation = EvaluationReservation(
            reservation_id="evaluation-" + "9" * 32,
            reservation_key="hidden:test",
            campaign_run_id="campaign-run",
            actor_id=None,
            artifact_ref=artifact,
            scope=EvaluationScope.HIDDEN,
            reserved_seconds=60,
            status=EvaluationReservationStatus.RESERVED,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_routes_scopes_through_separate_durable_lanes(self) -> None:
        visible_receipt = self.evaluator.visible_evaluate(
            self.candidate, self.visible_reservation, "visible:candidate"
        )
        hidden_receipt = self.evaluator.hidden_evaluate(
            self.candidate, self.hidden_reservation, "hidden:selection"
        )

        visible = self.evaluator.resolve(
            visible_receipt,
            self.candidate,
            self.visible_reservation,
            EvaluationScope.VISIBLE,
        )
        hidden = self.evaluator.resolve(
            hidden_receipt,
            self.candidate,
            self.hidden_reservation,
            EvaluationScope.HIDDEN,
        )

        self.assertEqual(visible.criterion_units, 1_000_000)
        self.assertEqual(hidden.criterion_units, 930_000)
        self.assertEqual(self.evaluator.used_seconds(visible_receipt), 7)
        self.assertEqual(self.evaluator.used_seconds(hidden_receipt), 11)

        restarted = SplitScopeServingEvaluator(
            self.root / "split.sqlite3",
            self.profile,
            self.visible,
            self.hidden,
        )
        self.assertEqual(
            restarted.visible_evaluate(
                self.candidate, self.visible_reservation, "visible:candidate"
            ),
            visible_receipt,
        )
        self.assertEqual(
            restarted.resolve(
                hidden_receipt,
                self.candidate,
                self.hidden_reservation,
                EvaluationScope.HIDDEN,
            ).criterion_units,
            930_000,
        )

    def test_cross_scope_and_receipt_tampering_fail_closed(self) -> None:
        receipt = self.evaluator.visible_evaluate(
            self.candidate, self.visible_reservation, "visible:tamper"
        )
        with self.assertRaisesRegex(RuntimeError, "binding differs"):
            self.evaluator.resolve(
                receipt,
                self.candidate,
                self.hidden_reservation,
                EvaluationScope.HIDDEN,
            )

        with closing(sqlite3.connect(self.root / "split.sqlite3")) as connection:
            connection.execute(
                "UPDATE split_evaluation_receipts SET lane_digest = ?",
                ("sha256:" + "0" * 64,),
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "binding differs"):
            self.evaluator.resolve(
                receipt,
                self.candidate,
                self.visible_reservation,
                EvaluationScope.VISIBLE,
            )

    def test_profile_requires_separate_workload_account_and_namespace(self) -> None:
        with self.assertRaisesRegex(ValueError, "accounts must differ"):
            RegisteredEvaluationProfile(
                profile_id="invalid-shared-account",
                campaign_manifest_digest=self.campaign.manifest_digest,
                registration_manifest_digest=digest_value({"study": "invalid"}),
                visible=self.visible_lane,
                hidden=self._lane(
                    EvaluationScope.HIDDEN,
                    self.hidden.profile_digest,
                    self.visible_lane.compute_account_id,
                    "different-hidden-evidence",
                    "different-hidden-workload",
                ),
            )
        with self.assertRaisesRegex(ValueError, "workloads must differ"):
            RegisteredEvaluationProfile(
                profile_id="invalid-shared-workload",
                campaign_manifest_digest=self.campaign.manifest_digest,
                registration_manifest_digest=digest_value({"study": "invalid"}),
                visible=self.visible_lane,
                hidden=self._lane(
                    EvaluationScope.HIDDEN,
                    self.hidden.profile_digest,
                    "different-hidden-account",
                    "different-hidden-evidence",
                    "public-workload",
                ),
            )

    def test_scope_ambiguous_evaluation_key_is_rejected_before_dispatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid for its scope"):
            self.evaluator.hidden_evaluate(
                self.candidate,
                self.hidden_reservation,
                "visible:not-hidden",
            )
        with self.assertRaisesRegex(ValueError, "invalid for its scope"):
            self.evaluator.visible_evaluate(
                self.candidate,
                self.visible_reservation,
                "hidden:not-visible",
            )

    @staticmethod
    def _lane(
        scope: EvaluationScope,
        evaluator_profile_digest: str,
        account_id: str,
        evidence_namespace: str,
        workload_id: str,
    ) -> EvaluationLaneProfile:
        return EvaluationLaneProfile(
            scope=scope,
            evaluator_profile_digest=evaluator_profile_digest,
            compute_backend_profile_digest=digest_value(
                {"backend": scope.value}
            ),
            workload_digest=digest_value({"workload": workload_id}),
            compute_account_id=account_id,
            schedule_digest=digest_value({"schedule": scope.value}),
            evidence_namespace=evidence_namespace,
        )


if __name__ == "__main__":
    unittest.main()
