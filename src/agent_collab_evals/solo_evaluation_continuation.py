"""Evaluation-only continuation of an aborted exploratory solo pilot.

The source run stays aborted. A continuation has separate frozen requests,
authorization, ledgers, and an explicitly non-scoreable mixed-build outcome.
No agent runtime or model API is constructed by this module.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, replace
from pathlib import Path

from .adapters.compute_candidate_evaluator import ComputeCandidateEvaluator
from .adapters.compute_quality_backend import ComputeQualityRepetitionBackend
from .adapters.sqlite_compute_routes import SqliteComputeRouteInventory
from .campaigns.serving_quality import evaluate_quality_series
from .campaigns.serving_scoring import RepetitionScore, summarize_candidate
from .canonical import digest_bytes, digest_file, digest_value, parse_json
from .compute_backend import ComputeExecutionRequest, ComputeExecutionStatus, FrozenComputeRunManifest
from .evaluation import EvaluationInProgress, EvaluationScope
from .pilot_evidence import retain_bytes, retain_document


def _pinned_document(path, digest):
    if digest_file(path) != digest:
        raise RuntimeError("continuation evidence digest differs")
    return parse_json(path.read_text())


def _slot(entry):
    adapter, key = entry["adapter_id"], entry["request"]["execution_key"]
    if adapter == "public":
        return "public-reference" if entry["request"]["campaign_run_id"] == "registered-reference" else "public-candidate"
    if adapter == "correctness" and key.endswith(":correctness"):
        return "correctness"
    match = re.search(r":quality:([1-3]):(reference|candidate)$", key)
    if adapter == "quality" and match:
        return f"quality-{match[1]}-{match[2]}"
    match = re.search(r":repetition:([1-3]):performance$", key)
    if match and adapter == f"performance-{match[1]}":
        return adapter
    raise ValueError("recovery entry is outside the solo evaluation scenario")


_ORDER = ("public-reference", "public-candidate", "correctness",
    "quality-1-reference", "quality-1-candidate", "quality-2-candidate",
    "quality-2-reference", "quality-3-reference", "quality-3-candidate",
    "performance-1", "performance-2", "performance-3")
_OUTSTANDING = _ORDER[6:]


class EvaluationContinuation:
    """One fixed six-job continuation, composed with durable compute adapters."""

    def __init__(self, root, recovery_path, recovery_digest, run_id, *, campaign,
                 adapters, quality_profile, phase_profiles, policy, scoring,
                 candidates, build_binding, execution_mode):
        self.root = Path(root).resolve()
        self.recovery_path = Path(recovery_path).resolve(strict=True)
        self.recovery_digest = recovery_digest
        self.recovery = _pinned_document(self.recovery_path, recovery_digest)
        source = Path(self.recovery["source_root"]).resolve(strict=True)
        if any(self.root.is_relative_to(path) or path.is_relative_to(self.root)
               for path in (source, self.recovery_path.parent)):
            raise ValueError("continuation state must be separate from source and recovery evidence")
        if (self.recovery.get("schema_version") != "solo-evaluation-recovery-plan/v1"
            or self.recovery.get("source_status") != "aborted"
            or self.recovery.get("scoreable") is not False
            or self.recovery.get("execution_authorized") is not False
            or run_id == self.recovery["source_run_id"]
            or execution_mode not in {"live", "no_spend"}):
            raise ValueError("continuation requires a distinct exploratory recovery")
        self.campaign, self.quality_profile = campaign, quality_profile
        self.phase_profiles, self.policy, self.scoring = dict(phase_profiles), policy, scoring
        self.candidates = dict(candidates)
        if (set(self.phase_profiles) != {"correctness", "performance-1", "performance-2", "performance-3"}
            or quality_profile.repetitions != 3
            or quality_profile.quality_profile_digest != policy.quality_profile_digest
            or quality_profile.quality_workload_digest != policy.quality_workload_digest
            or quality_profile.compute_execution_profile_digest != adapters["quality"].backend_profile_digest
            or any(profile.compute_execution_profile_digest != adapters[key].backend_profile_digest
                for key, profile in self.phase_profiles.items())):
            raise ValueError("continuation scoring and compute profiles differ")
        entries = self.recovery["entries"]
        self.entries = {_slot(entry): entry for entry in entries}
        if len(entries) != 12 or set(self.entries) != set(_ORDER):
            raise ValueError("continuation requires the complete twelve-job solo inventory")
        if any(self.entries[slot]["action"] != "reuse_verified" for slot in _ORDER[:6]):
            raise ValueError("continuation requires six verified source results")
        if (self.entries[_ORDER[6]]["action"] != "replace_environment_rejected"
            or any(self.entries[slot]["action"] != "execute_missing" for slot in _ORDER[7:])):
            raise ValueError("continuation replacement policy differs")
        self._check_source()
        expected_candidates = {entry["request"]["candidate_digest"] for entry in entries}
        if set(self.candidates) != expected_candidates:
            raise ValueError("continuation candidate inventory differs")
        for digest, content in self.candidates.items():
            descriptor = campaign.validate_candidate_document(parse_json(content.decode()))
            if digest_bytes(content) != digest or any(
                entry["request"]["candidate_manifest_digest"] != descriptor.manifest_digest
                for entry in entries if entry["request"]["candidate_digest"] == digest):
                raise RuntimeError("continuation candidate identity differs")
        reference_digest = digest_bytes(campaign.reference_candidate_path.read_bytes())
        if self.entries["public-reference"]["request"]["candidate_digest"] != reference_digest:
            raise RuntimeError("recovery public reference is not the registered reference")
        public = {slot: self._retained(self.entries[slot]) for slot in _ORDER[:2]}
        scores = {slot: self._public_score(value) for slot, value in public.items()}
        # The solo selector prefers the eligible reference on a tie. Recompute
        # this from verified public evidence, not mutable submission result JSON.
        reference, candidate = scores["public-reference"], scores["public-candidate"]
        winner = ("public-reference" if reference[0] and
            (not candidate[0] or reference[1] >= candidate[1]) else "public-candidate")
        if not scores[winner][0]:
            raise RuntimeError("recovery has no eligible public selection")
        selected = self.entries[winner]["request"]["candidate_digest"]
        for slot in _ORDER[2:]:
            expected = reference_digest if slot.endswith("-reference") else selected
            if self.entries[slot]["request"]["candidate_digest"] != expected:
                raise RuntimeError("hidden candidate differs from the authoritative public selection")
        # Validate semantic pins before creating any new compute state. Original
        # transport/evaluator provenance remains in each source entry.
        for slot in _ORDER[2:6]:
            self._validate_result(slot, self._source_request(slot), self._retained(self.entries[slot]))
        self.requests = {}
        for slot in _OUTSTANDING:
            entry = self.entries[slot]
            original = self._source_request(slot)
            profile = quality_profile if entry["adapter_id"] == "quality" else self.phase_profiles[slot]
            self.requests[slot] = replace(original, campaign_run_id=run_id,
                reservation_id="continuation-" + digest_value({"run_id": run_id, "source_request": original.request_digest})[7:39],
                execution_key=run_id + ":" + original.execution_key.split(":", 1)[-1],
                evaluator_profile_digest=profile.digest, scope=EvaluationScope.HIDDEN)
        self.inventory = SqliteComputeRouteInventory(self.root / "compute", run_id, adapters)
        for adapter_id in ("quality", "performance-1", "performance-2", "performance-3"):
            self.inventory.register(adapter_id, tuple(self.requests[slot] for slot in _OUTSTANDING
                if self.entries[slot]["adapter_id"] == adapter_id))
        seal = self.inventory.seal()
        self.document = {"schema_version": "solo-evaluation-continuation/v1", "run_id": run_id,
            "execution_mode": execution_mode, "scoreable": False, "agent_reruns": 0, "model_calls": 0,
            "source_recovery_plan": {"file": str(self.recovery_path), "digest": recovery_digest},
            "source_audit_digest": self.recovery["source_audit_digest"], "source_status": "aborted",
            "selected_candidate_digest": selected, "build_binding": build_binding,
            "quality_profile": quality_profile, "phase_profiles": self.phase_profiles,
            "quality_policy": {key: value for key, value in asdict(policy).items() if key != "path"},
            "scoring_profile_digest": scoring.digest,
            "compute_inventory_digest": seal,
            "jobs": [{"slot": slot, "source": self.entries[slot],
                "replacement_request": self.requests[slot].document if slot in self.requests else None}
                for slot in _ORDER],
            "candidates": [{"digest": digest, "file": "candidates/" + digest[7:] + ".json"}
                for digest in sorted(self.candidates)]}
        for digest, content in self.candidates.items():
            retain_bytes(self.root / "candidates" / (digest[7:] + ".json"), content)
        self.digest = retain_document(self.root / "manifest.json", self.document)

    def _source_request(self, slot):
        return ComputeExecutionRequest.from_document(self.entries[slot]["request"])

    def _check_source(self):
        plan = _pinned_document(self.recovery_path, self.recovery_digest)
        if plan != self.recovery:
            raise RuntimeError("source recovery plan changed")
        root = Path(plan["source_root"])
        audit = _pinned_document(root / "audit.json", plan["source_audit_digest"])
        if (audit.get("status") != "aborted" or audit.get("scoreable") is not False
            or audit.get("run_config_digest") != plan["source_run_config_digest"]):
            raise RuntimeError("source campaign is no longer the pinned aborted run")
        _pinned_document(root / "run-config.json", plan["source_run_config_digest"])
        for entry in plan["entries"]:
            request = ComputeExecutionRequest.from_document(entry["request"])
            if request.request_digest != entry["request_digest"]:
                raise RuntimeError("source request digest differs")
            route = entry["route_id"]
            if not isinstance(route, str) or not re.fullmatch(r"[0-9a-f]{64}", route):
                raise ValueError("source route identity is invalid")
            manifest = FrozenComputeRunManifest.load(root / "evaluation/compute/routes" / route / "manifest.json",
                expected_digest=entry["source_manifest_digest"])
            manifest.assert_authorized(request)
            if entry["adapter_id"] != "public":
                profile = (self.quality_profile if entry["adapter_id"] == "quality"
                    else self.phase_profiles[entry["adapter_id"]])
                original_profile = replace(profile, compute_execution_profile_digest=manifest.backend_profile_digest)
                if original_profile.digest != request.evaluator_profile_digest:
                    raise RuntimeError("source evaluator semantic identity differs from continuation")
            if entry["action"] == "reuse_verified":
                self._retained(entry)

    def _retained(self, entry):
        ref = entry["retained_evidence"]
        path = (self.recovery_path.parent / ref["file"]).resolve(strict=True)
        path.relative_to(self.recovery_path.parent)
        evidence = _pinned_document(path, ref["digest"])
        request = ComputeExecutionRequest.from_document(entry["request"])
        expected = {"request_digest": request.request_digest, "candidate_digest": request.candidate_digest,
            "candidate_manifest_digest": request.candidate_manifest_digest,
            "evaluator_profile_digest": request.evaluator_profile_digest,
            "transport_profile_digest": entry["source_transport_profile_digest"],
            "evidence_profile_digest": entry["source_evidence_profile_digest"],
            "external_call_id": entry["source_call_id"], "status": "complete", "failure": None,
            "used_seconds": entry["observed_used_seconds"]}
        if (any(evidence.get(key) != value for key, value in expected.items())
            or type(evidence.get("used_seconds")) is not int
            or not 0 <= evidence["used_seconds"] <= request.maximum_seconds):
            raise RuntimeError("reused evidence identity or duration differs")
        return evidence

    @staticmethod
    def _public_score(evidence):
        result = evidence.get("result", {})
        score = result.get("performance_score", {})
        if (result.get("valid") is not True or type(score.get("eligible")) is not bool
            or type(score.get("scalar_ppm")) is not int):
            raise RuntimeError("public selection evidence is invalid")
        return score["eligible"], score["scalar_ppm"]

    def _validate_result(self, slot, request, evidence):
        if slot.startswith("quality-"):
            _, repetition, role = slot.split("-", 2)
            return ComputeQualityRepetitionBackend.validate_evidence(
                self.quality_profile, evidence, request, role, int(repetition))
        return ComputeCandidateEvaluator.validate_evidence(self.phase_profiles[slot], evidence, request)

    def _check_manifest(self, expected_digest):
        if expected_digest != self.digest or digest_file(self.root / "manifest.json") != self.digest:
            raise RuntimeError("continuation manifest differs from approved authority")
        self._check_source()
        for digest, content in self.candidates.items():
            if (digest_bytes(content) != digest
                or digest_file(self.root / "candidates" / (digest[7:] + ".json")) != digest):
                raise RuntimeError("retained continuation candidate changed")

    def run(self, *, expected_digest, admit, collection_seconds):
        """Execute only frozen replacements after an independent spend admission.

        The trusted host's admission callback must reserve dollars and issue the
        exact durable request authority. This runner cannot issue it itself.
        Resume reuses completed requests and collects existing calls; a failed
        or ambiguous request cannot be automatically replaced.
        """
        self._check_manifest(expected_digest)
        if type(collection_seconds) is not int or not 0 <= collection_seconds <= 300:
            raise ValueError("continuation collection limit is invalid")
        for slot in _OUTSTANDING:
            request = self.requests[slot]
            admit(self, request)
            self.inventory.require_authorized((request,))
            backend = self.inventory.backend(self.entries[slot]["adapter_id"])
            execution = backend.submit(request, self.candidates[request.candidate_digest])
            deadline = time.monotonic() + request.maximum_seconds + 60
            while execution.status in {ComputeExecutionStatus.REGISTERED,
                    ComputeExecutionStatus.DISPATCHING, ComputeExecutionStatus.DISPATCHED}:
                remaining = max(0, round(deadline - time.monotonic()))
                if remaining == 0:
                    raise EvaluationInProgress("continuation has an in-progress call; resume collection, not dispatch")
                if execution.status is ComputeExecutionStatus.DISPATCHED:
                    execution = backend.collect(request, timeout_seconds=min(collection_seconds, remaining))
                else:
                    time.sleep(0.05)
                    execution = backend.submit(request, self.candidates[request.candidate_digest])
            if execution.status is not ComputeExecutionStatus.COMPLETE:
                raise RuntimeError(f"continuation stopped: {execution.status.value}:{execution.failure}")
            _, evidence = backend.resolve(request)
            self._validate_result(slot, request, evidence)
            retain_document(self.root / "completed" / (request.request_digest[7:] + ".json"), evidence)
        return self.close(expected_digest=expected_digest)

    def close(self, *, expected_digest):
        """Reconcile all six new executions and all six reused results before scoring."""
        self._check_manifest(expected_digest)
        self.inventory.require_authorized(tuple(self.requests.values()), consumed=True)
        resolved = {}
        receipts = []
        for source in self.inventory.sources():
            planned = {request.request_digest: request for request in source.manifest.requests()}
            actual = source.backend.reconcile(source.manifest.campaign_run_id)
            if len(actual) != len(planned) or {r.request_digest for r in actual} != set(planned):
                raise RuntimeError("continuation closure request set differs")
            for receipt in actual:
                if receipt.status is not ComputeExecutionStatus.COMPLETE:
                    raise RuntimeError("continuation closure contains nonterminal or failed compute")
                request = planned[receipt.request_digest]
                _, evidence = source.backend.resolve(request)
                if evidence["used_seconds"] > request.maximum_seconds:
                    raise RuntimeError("continuation exceeded compute allowance")
                resolved[receipt.request_digest] = evidence
                receipts.append(receipt)
        if set(resolved) != {request.request_digest for request in self.requests.values()}:
            raise RuntimeError("continuation closure inventory differs")
        results, provenance = {}, []
        for slot in _ORDER:
            entry = self.entries[slot]
            request = self.requests.get(slot, self._source_request(slot))
            evidence = resolved[request.request_digest] if slot in self.requests else self._retained(entry)
            if slot not in _ORDER[:2]:
                results[slot] = self._validate_result(slot, request, evidence)
            provenance.append({"slot": slot, "origin": "continuation" if slot in self.requests else "source",
                "request_digest": request.request_digest, "evidence_digest": digest_value(evidence),
                "evaluator_profile_digest": request.evaluator_profile_digest,
                "transport_profile_digest": evidence["transport_profile_digest"],
                "evidence_profile_digest": evidence["evidence_profile_digest"],
                "external_call_id": evidence["external_call_id"], "used_seconds": evidence["used_seconds"]})
        quality = evaluate_quality_series(self.policy,
            [results[f"quality-{r}-reference"] for r in range(1, 4)],
            [results[f"quality-{r}-candidate"] for r in range(1, 4)])
        performance = summarize_candidate(self.scoring, tuple(RepetitionScore(r,
            results[f"performance-{r}"].eligible, results[f"performance-{r}"].criterion_units,
            {}, {}, results[f"performance-{r}"].failures) for r in range(1, 4)))
        outcome = {"schema_version": "solo-evaluation-continuation-outcome/v1", "status": "complete",
            "run_id": self.document["run_id"], "manifest_digest": self.digest,
            "source_run_id": self.recovery["source_run_id"], "source_status": "aborted",
            "scoreable": False, "interpretation": "exploratory_mixed_build_evaluation_only",
            "agent_reruns": 0, "model_calls": 0, "reused_executions": 6, "new_executions": 6,
            "eligible": results["correctness"].eligible and quality["eligible"] and performance.eligible,
            "correctness": results["correctness"], "quality": quality,
            "performance": performance.to_document(), "compute_receipts": receipts, "provenance": provenance}
        self._check_manifest(expected_digest)
        retain_document(self.root / "outcome.json", outcome)
        return outcome
