"""Exploratory evaluator using one physical job for each stock/candidate pair."""

from pathlib import Path
import time

from ..canonical import digest_bytes, digest_value, parse_json
from ..compute_backend import ComputeExecutionRequest, ComputeExecutionStatus
from ..campaigns.serving_paired_performance import FUNCTION_SECONDS, summarize_pairs
from ..campaigns.serving_paired_profile import HIDDEN_SLOTS
from ..campaigns.serving_quality import evaluate_quality_series
from ..evaluation import EvaluationInProgress, EvaluationReceipt, EvaluationResult, EvaluationScope
from ..pilot_evidence import retain_document


class PairedServingEvaluator:
    """Reuse budget, submission, and closure ports with co-located evidence.

    Visible selection uses one warm performance pair. Hidden evaluation retains
    correctness on both roles, three quality pairs under the frozen margins,
    and three warm performance pairs. Every resolve reads the raw-backed compute
    receipts again; a mutable submission score cannot become authority.
    """

    def __init__(self, root, profile, backend, scope):
        self.root, self.profile, self.backend, self.scope = Path(root), profile, backend, scope
        self.profile_digest = profile.evaluator_digest(scope)
        self.reserved_seconds = FUNCTION_SECONDS * (1 if scope is EvaluationScope.VISIBLE else len(HIDDEN_SLOTS))
        self._used = {}

    @staticmethod
    def _reservation_binding(reservation):
        if reservation is None:
            return None
        return {key: getattr(reservation, key) for key in ("reservation_id", "reservation_key", "campaign_run_id",
            "actor_id", "artifact_ref", "scope", "reserved_seconds")}

    def _requests(self, candidate, reservation, evaluation_key, scope):
        self.profile.check_inputs()
        if scope is not self.scope or not evaluation_key.startswith(scope.value + ":"):
            raise ValueError("paired evaluation scope/key differs")
        if reservation is None:
            if scope is not EvaluationScope.VISIBLE or candidate != self.profile.campaign.reference_candidate_path.read_bytes():
                raise PermissionError("unreserved evaluation is only for the stock public reference")
        elif (reservation.scope is not scope or reservation.reserved_seconds != self.reserved_seconds):
            raise ValueError("paired evaluation reservation differs")
        descriptor = self.profile.campaign.validate_candidate_document(parse_json(candidate.decode()))
        identity = digest_value({"evaluation_key": evaluation_key, "evaluator": self.profile_digest,
            "candidate": digest_bytes(candidate), "reservation": self._reservation_binding(reservation)})[7:39]
        return tuple(ComputeExecutionRequest(execution_key="paired-" + identity + ":paired-" + slot,
            campaign_run_id=reservation.campaign_run_id if reservation else "registered-reference",
            reservation_id=reservation.reservation_id if reservation else "reference-" + identity,
            scope=scope, candidate_digest=digest_bytes(candidate), candidate_manifest_digest=descriptor.manifest_digest,
            evaluator_profile_digest=self.profile_digest, maximum_seconds=FUNCTION_SECONDS)
            for slot in (("public-1",) if scope is EvaluationScope.VISIBLE else HIDDEN_SLOTS))

    def prepare_visible_request(self, candidate, reservation, evaluation_key):
        return self._requests(candidate, reservation, evaluation_key, EvaluationScope.VISIBLE)[0]

    def prepare_hidden_requests(self, candidate, reservation, evaluation_key):
        return self._requests(candidate, reservation, evaluation_key, EvaluationScope.HIDDEN)

    def visible_evaluate(self, candidate, reservation, evaluation_key):
        return self._evaluate(candidate, reservation, evaluation_key, EvaluationScope.VISIBLE)

    def hidden_evaluate(self, candidate, reservation, evaluation_key):
        return self._evaluate(candidate, reservation, evaluation_key, EvaluationScope.HIDDEN)

    def _evaluate(self, candidate, reservation, evaluation_key, scope):
        requests = self._requests(candidate, reservation, evaluation_key, scope)
        for request in requests:
            execution = self.backend.submit(request, candidate)
            # Function-body accounting excludes provider startup and collection.
            # Bound wall waiting separately so a slow startup is not mislabeled
            # as a terminal function failure or an excuse to dispatch twice.
            deadline = time.monotonic() + FUNCTION_SECONDS + 1200
            while execution.status in {ComputeExecutionStatus.REGISTERED,
                ComputeExecutionStatus.DISPATCHING, ComputeExecutionStatus.DISPATCHED}:
                if time.monotonic() >= deadline:
                    raise EvaluationInProgress("paired evaluation remains in flight; reconnect to the same request")
                if execution.status is ComputeExecutionStatus.DISPATCHED:
                    execution = self.backend.collect(request, timeout_seconds=60)
                else:
                    time.sleep(0.05)
                    execution = self.backend.submit(request, candidate)
            if execution.status is not ComputeExecutionStatus.COMPLETE:
                raise RuntimeError("paired evaluation failed: " + execution.status.value + ":" + str(execution.failure))
        context = {"schema_version": "paired-evaluator-receipt/v1", "evaluator_profile_digest": self.profile_digest,
            "candidate_digest": digest_bytes(candidate), "reservation": self._reservation_binding(reservation),
            "evaluation_key": evaluation_key, "requests": [r.document for r in requests]}
        receipt = EvaluationReceipt("evalreceipt-" + digest_value(context)[7:39])
        # Validate the raw-backed result before publishing an opaque receipt.
        self._result(requests, candidate)
        retain_document(self.root / "receipts" / (receipt.value + ".json"), context)
        return receipt

    def resolve(self, receipt, candidate, reservation, scope):
        context = parse_json((self.root / "receipts" / (receipt.value + ".json")).read_text())
        requests = self._requests(candidate, reservation, context["evaluation_key"], scope)
        expected = {"schema_version": "paired-evaluator-receipt/v1", "evaluator_profile_digest": self.profile_digest,
            "candidate_digest": digest_bytes(candidate), "reservation": self._reservation_binding(reservation),
            "evaluation_key": context["evaluation_key"], "requests": [r.document for r in requests]}
        if digest_value(context) != digest_value(expected) or receipt.value != "evalreceipt-" + digest_value(expected)[7:39]:
            raise RuntimeError("paired evaluator receipt binding differs")
        result, used = self._result(requests, candidate)
        self._used[receipt.value] = used
        return result

    def used_seconds(self, receipt):
        if receipt.value not in self._used:
            raise RuntimeError("paired receipt must be resolved before accounting")
        return self._used[receipt.value]

    def _result(self, requests, candidate):
        results, evidence_pins, used = {}, {}, 0
        for request in requests:
            execution, evidence = self.backend.resolve(request)
            if execution.status is not ComputeExecutionStatus.COMPLETE or execution.used_seconds is None:
                raise RuntimeError("paired result is not complete")
            if execution.used_seconds > request.maximum_seconds:
                raise RuntimeError("paired compute exceeded its fixed allowance")
            if (evidence.get("request_digest") != request.request_digest
                or evidence.get("candidate_digest") != digest_bytes(candidate)
                or set(evidence.get("result", {})) != {"paired_evaluation"}):
                raise RuntimeError("paired evaluation envelope differs")
            value = evidence["result"]["paired_evaluation"]
            if value.get("scoreable") is not False:
                raise RuntimeError("paired exploratory result cannot be scoreable")
            results[self.profile.slot(request)] = value
            evidence_pins[request.request_digest] = digest_value(evidence)
            used += execution.used_seconds
        if self.scope is EvaluationScope.VISIBLE:
            pair = results["public-1"]
            # Stock is the neutral ratio denominator, not a noisy estimate of
            # itself. It is still executed and checked for SLO eligibility.
            score = pair["scores"]["reference" if candidate == self.profile.campaign.reference_candidate_path.read_bytes() else "candidate"]
            failures = list(score["failures"])
            if pair.get("eligible") is not True:
                failures.append("public_reference_or_candidate_ineligible")
            scalar = score["scalar_ppm"]
            diagnostics = {"paired": True, "driver_version": pair["driver_version"], "scoreable": False}
        else:
            correctness = results["correctness-1"]["scores"]
            failures = []
            for role in ("reference", "candidate"):
                if correctness[role]["eligible"] is not True:
                    failures.append(role + "_correctness_failed")
            quality = evaluate_quality_series(self.profile.quality_policy,
                [results[f"quality-{r}"]["scores"]["reference"] for r in range(1, 4)],
                [results[f"quality-{r}"]["scores"]["candidate"] for r in range(1, 4)])
            performance = summarize_pairs([results[f"performance-{r}"] for r in range(1, 4)])
            failures.extend(quality["failures"])
            if performance["eligible"] is not True:
                failures.append("hidden_reference_or_candidate_performance_ineligible")
            scalar = performance["median_candidate_reference_ratio_ppm"]
            diagnostics = {"correctness": {role: {k: correctness[role][k]
                for k in ("eligible", "passed_cases", "total_cases")} for role in correctness},
                "quality": quality, "performance": performance, "scoreable": False,
                "pair_drivers": {slot: value["driver_version"] for slot, value in results.items()}}
        return EvaluationResult(eligible=not failures, criterion_units=scalar,
            failures=tuple(dict.fromkeys(failures)), evidence_digest=digest_value({"evidence": evidence_pins,
                "profile": self.profile_digest, "candidate": digest_bytes(candidate)}), diagnostics=diagnostics), used
