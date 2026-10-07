# Matched exploratory peer pilot

Updated: October 7, 2026.

Next question: does communication improve the selected solution at matched
model/provider, task material, budgets, and evaluation rules? Compare
`peer_collab` with `peer_isolated` first. This is exploratory feasibility work,
not registration or evidence that collaboration already helps.

## Completed

- The [solo exploratory outcome](../evidence/solo_pilot/20261005-paired-performance/README.md)
  completed and reconciled. The candidate was ineligible and had negligible
  measured speedup; a negative result does not block testing the peer formats.
- Both peer arms reuse the solo candidate services, public handoff, neutral
  selector, hidden evaluator stack, and closure gates. There is no new
  orchestration platform or evaluator implementation.
- The bounded path admits exactly one candidate and one public evaluation per
  actor. Four actors receive equal partitions of the aggregate model and public
  compute budgets, plus reserved actor storage. Receipts and results stay
  owner-private; common feedback contains no peer receipts or result digests.
- Both arms receive identical material, prompts, candidate tools, and peer tools.
  Only backend visibility changes. No roles, methods, or coordination structure
  are assigned. This small configuration-only task is not the full intended
  model-optimization interface or a test of unrestricted agent specialization.
- Both formats passed the complete no-spend lifecycle with real OpenCode inside
  the existing rootless OCI sandbox: 32 synthetic model calls, 15 synthetic
  compute executions, valid model/compute closure, and no remaining containers
  per arm. Isolated peers had zero cross-actor reads; collaborative peers had 24.
  [Retained observations](../evidence/deployment/peer-candidate-nospend-20261005.json)
  bind the same task material, peer profile, and implementation source digest.
  These observations record working-tree code, not a committed deployment.
- Regression tests cover owner-private release, pending evaluation and restart,
  reference-winner hidden evaluation, unequal-budget rejection, missing actors,
  failed actor evaluation, and independent per-actor usage reconciliation.
- A no-spend raw replay verifies the six frozen quality-calibration receipts
  and the earlier stock correctness control. Quality passes at 166/192 versus
  168/192 for reference; stock correctness passes 8/8. The
  [answer-free review](../evidence/calibration/reference-controls-review-20261005.json)
  records that correctness used the older hidden workload, quality used an
  older campaign build, and all seven calls used driver `580.95.05`.
  This is historical calibration, not qualification of the current control or
  newer-driver quality. No thresholds, workloads, or old outcomes changed.
- The performance follow-up now uses a shared pair-policy validator. It checks
  the specification digest, declared drivers, alternating role order, pinned
  software, and unchanged GPU observations. The ordinary live phase factories
  still use their exact-driver policy; the validator does not silently relax
  those factories or authorize a paid peer run.
- A separate paired live-composition factory now reuses the existing durable
  backend, candidate services, selector, handoff, runner, and closure. Each
  public measurement, hidden correctness measurement, quality repetition, and
  performance repetition runs stock and candidate in one restricted L4 job.
  Both declared drivers are checked consistently; the stock/candidate roles
  must have unchanged GPU observations and software. Each role has a fresh
  process and engine cache. The old exact-driver solo path is unchanged.
- The real factories pass no-spend tests with simulated raw GPU output: four
  actor submissions, private public feedback, neutral selection, one hidden
  correctness pair, three quality pairs, three performance pairs, and closure
  after a fresh restart without redispatch. There are 12 physical executions,
  not the 15 separate synthetic executions in the earlier OpenCode deployment
  observation. No new live GPU or OpenCode/paired-evaluator deployment is
  claimed. The Modal wrapper imports and serializes with SDK 1.5.4 locally.
- `peer-pilot --check` constructs this composition and its request inventory
  without keys or spending authority. The two live-composition candidates
  differ only in condition. Each proposes four actors, a $0.30 aggregate model
  allowance partitioned equally, and one 3,000-second public pair per actor.
  Hidden evaluation has seven 3,000-second physical pairs. All raw-derived
  outcomes remain exploratory and non-scoreable.
- `prepare-peer-stock-control` freezes the seven stock-versus-stock requests,
  their reservation, route manifest, and sealed inventory. It constructs the
  durable authorization store but leaves every request unissued. Preparation
  cannot reuse an authorized/dispatched control. A current-workload inventory
  is retained locally; no stock-control result or remote conformance exists yet.
- Commit `9d0aa98` is deployed in a separate dedicated-VM checkout. Its 11 paired
  factory tests pass there without paid execution. The original live checkout,
  solo outcome, receipts, and cumulative journal are unchanged.
- `run-peer-stock-control` now binds the clean commit, configuration,
  preparation, seven requests, sealed inventory, and preceding journal snapshot
  to independently pinned, expiring operator authority. One atomic currency
  batch includes all seven jobs and overhead before any compute grant. It
  preserves old reserves and releases, permits only explicit approved ceiling
  changes, and disables unpinned readers from admitting more work. Restart
  re-resolves completed evidence; uncertain calls are not replaced or cancelled
  merely because the controller stopped. These are no-spend tests, not current
  stock qualification or live wrapper conformance.

The no-spend configs differ only in `condition`:
`config/pilots/peer-isolated-no-spend-oci-v1.json` and
`config/pilots/peer-collab-no-spend-oci-v1.json`. Their dollar figures are synthetic
test limits, not proposed paid allowances. `peer-pilot` cannot authorize live
execution. OCI execution requires explicit host runtime dependencies.

## Next, before any paid peer comparison

1. Retain the completed solo billing settlement when attributable provider
   evidence is available. Never release historical reserves without it.
   No new journal or implicit cap reset. The tested paired composition is
   committed and separately deployed; the stock runner is the next checkpoint.
2. Qualify the actual
   restricted Modal wrapper. The new public/correctness/quality/performance
   factories share the declared same-GPU policy; the old solo factories remain
   exact-driver. Never silently mix those paths. Local imports, serialization,
   and simulated receipts do not prove remote deployment conformance.
3. Check the stock reference under the intended hidden correctness/quality
   gates, including a clean reference-versus-reference control where relevant.
   The retained calibration is independently replayed, but its correctness
   workload and build do not fully match the next pilot. No new live hidden
   stock control was run in this step. Preserve the old outcome;
   any calibration change creates a new version, not a favorable rescoring.
4. Extend request-bound dollar authority to the matched peer composition.
   The stock runner now implements it for the exact seven-job control, without
   approval or execution. The peer command still cannot authorize paid execution.
   Bound every actor's public evaluation, shared
   hidden evaluation, startup, cleanup, and failures. Review provider/cache
   isolation and the shared-GPU timing limitation for this exploratory scope.
   Batched private release and equal seconds budgets are not proof of a
   deterministic wall-clock scheduler or timing-channel elimination.
5. Finalize the costed matched-pair proposal and request fresh spending approval.
   Reconcile the shared cumulative journal first. Then run
   human-observed pilots and report quality eligibility first, performance,
   API/GPU cost, elapsed time, and failures. One pair is feasibility evidence,
   not a causal-effect estimate or statistical significance claim.

Add `native_multiagent` after its candidate transport and child-admission path
are qualified in this deployment. Registered four-condition experiments still
require the unresolved registration authorities. Do not expand into general
product features, new harnesses, or large-fleet hardening before this pilot.

## No-spend acceptance

The deployment test uses the existing Podman image and pinned runtime. In the
dedicated Linux VM, run it from an isolated checkout with dependencies installed:

```bash
RUN_OCI_PILOT_INTEGRATION=1 python -W error::ResourceWarning -m unittest \
  tests.test_oci_pilot.OciPilotIntegrationTests.test_complete_matched_peer_pilots_without_model_or_gpu_spend -v
```

The ordinary suite exercises the same lifecycle with a fake runtime. Neither
test substitutes synthetic success for live agent optimization evidence.

`review-reference-controls` takes explicit quality/control paths and pinned
source/target bundle digests. It rederives scores from raw responses, checks
the retained seals, and optionally retains a write-once answer-free report.
It never dispatches compute, reads credentials, or issues spending authority.
The six quality receipt anchors come from the frozen policy; the correctness
anchor must be supplied by the operator from retained evidence, not accepted
from the receipt under review.

Run the actual paired factory integration without spending:

```bash
.venv/bin/python -W error::ResourceWarning -m unittest tests.test_paired_serving_stack -v
.venv/bin/python -m agent_collab_evals peer-pilot --check \
  --config config/pilots/peer-isolated-live-composition-v1.json \
  --state-root tmp/peer-composition-checks --run-id isolated-check
.venv/bin/python -m agent_collab_evals prepare-peer-stock-control \
  --state-root tmp/peer-stock-controls --run-id stock-control-preparation
```

The configuration check requires the evaluator-private workload bundle and the
project-local Modal installation. It cannot substitute for runtime conformance
or an operator authorization. Use a new run ID after changing source or inputs;
retained checks are write-once. The factory pins the implementation and resource
files, isolates remote evidence namespaces across runs, consumes durable
single-use compute authority before subprocess launch, and reconnects only to
the same retained call. Measured duration is not clamped to the reservation.

## Cost proposal, not spending authority

The reviewed resource allocation includes GPU, CPU, memory, startup, cleanup,
an evidence helper, and shared overhead. The following are admission allowances,
not expected charges or provider-enforced billing caps:

| Stage | Physical GPU jobs | Modal allowance | API allowance |
| --- | ---: | ---: | ---: |
| Stock hidden control | 7 | $8.965888 | $0 |
| One four-actor arm | 12 | $14.655808 | $0.30 |
| Matched pair | 24 | $29.311616 | $0.60 |

These figures use `config/compute/modal-pilot-cost-v1.json`. Its L4, CPU, and
memory rates still match [Modal's published pricing](https://modal.com/pricing)
when checked on October 6, 2026. Reverify rates and provider routing before
issuing approval; do not infer available funds from these calculations.
Historical reserves and provider settlement remain separate obligations.

The operator preparation succeeded locally for `stock-control-final-1006`:
seven frozen requests, zero authorizations, and zero dispatches. Its write-once
manifest is under `.private/peer-paired-preparation/stock-control-final-1006/`.
This working-tree preparation must be regenerated against the reviewed clean
deployment before any paid authority is issued. Local CLI output also confirms
that both peer candidates have the same request-inventory digest and budget
geometry. Paid candidate digests are frozen after admission, not replaced by
the stock bytes used for the offline composition check.

The proposed actor sandbox lifetime must cover the entire sequential evaluation
schedule, including provider startup and collection. The offline check computes
48,600 seconds for four actors at the current 900-second agent deadline.
Neither the old sandbox candidate nor an arbitrary timeout edit qualifies that
deployment. A matching pinned development profile and conformance check remain
required. Do not raise caps, issue allowances, or run either stage until the
run-bound authority path and deployment checks pass and the user approves it.
