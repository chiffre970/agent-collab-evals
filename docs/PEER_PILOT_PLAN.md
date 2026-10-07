# Matched exploratory peer pilot

Updated: October 8, 2026.

Next question: does communication improve the selected solution at matched
model/provider, task material, budgets, and evaluation rules? Compare
`peer_collab` with `peer_isolated` first. This is exploratory feasibility work,
not registration or evidence that collaboration already helps.

## Current live stock control

On October 7, the user approved one seven-job stock control with cumulative
ceilings of $29 Modal and $3.10 OpenRouter. The live prelaunch check passed for
the pinned build, unused inventory, unchanged journal, rate card, and idle dev
environment. The user confirmed a separate $20 workspace usage limit, covering
the $4.64265009 prior October usage plus the $8.965888 control allowance.
`stock-control-1007b` launched once at `2026-10-07T10:11:18Z`, with fresh
18-hour authority and one atomic debit in the original journal. Cumulative
Modal allowances are $28.81325448 under the approved $29 ceiling; all historical
reserves remain held. OpenRouter remains $3.10, with no new API allowance.

The control completed all seven jobs with no stop records. Raw replay verifies
every execution and reproduces the final outcome. The median stock/stock
performance ratio is 0.999860, or -0.014%, but overall eligibility is false.
Both stock roles pass 7/8 correctness checks. The failing arithmetic case has
the correct sum in a full equation instead of the required bare integer;
this is a strict response-format failure, not incorrect arithmetic.

Quality totals are equal at 165/192. All six pass/fail disagreements involve
a generation hitting the 4,096-token limit without a final answer. The
reasoning-family bootstrap resamples 16 case clusters, not 48 independent cases.
Its lower confidence bound is -10.4167 percentage points against a 6.25-point
margin. An independent discrete-bootstrap calculation reproduces that bound;
there is no identified scoring or quantile bug. The control does not establish
quality preservation, and it does not demonstrate degradation by an optimizer.
See the [answer-free diagnosis](../evidence/calibration/stock-control-diagnosis-20261008.json).

The restricted wrapper's seven-job execution path is now demonstrated live;
current reference eligibility is not qualified. Preserve this result and its
frozen inputs. No gate, old score, or spending authority has changed. Provider
billing settlement remains pending; 7,251 function-body seconds are not a bill.

## Immediate calibration checkpoint

The V3 calibration is implemented and prepared locally without paid execution.
It reuses the existing factories, durable compute backend, and currency runner;
it does not introduce another evaluator or orchestration platform.

- Arithmetic checks now measure semantic accuracy separately from formatting.
  They accept a bare correct integer or a complete, correct addition equation
  with the expected operands. Wrong sums, unrelated operands, prose, malformed
  API responses, and truncated generations fail. Exact echo checks stay exact.
- Thinking responses have an 8,192-token completion cap, a 16,384-token stock
  context, and a 600-second request timeout. Nonthinking decoding, all cases,
  prompts, seeds, margins, and the confidence rule remain unchanged.
- The new quality policy is explicitly pending current calibration. Historical
  control receipts are not copied into it as V3 qualification evidence.
- CPU preflight uses the pinned Qwen tokenizer and chat template. Every request
  fits; the largest prompt-plus-completion allowance is 8,390 tokens. No model
  weights or GPU execution are needed for this check.
- The diagnostic inventory contains only `correctness-1` and `quality-1`:
  two physical stock/stock jobs, a 6,000-second reservation, a $3.275968 Modal
  admission allowance, and no model API allowance. Preparation issues no grants.
  The scoped runner requires an independently pinned context check before
  admission and cannot report full qualification from this single repetition.

The 3,000-second cap per function bounds exposure, not completion at every
request's maximum timeout. The quality request-timeout envelope is 9,600
seconds before engine startup; a timeout remains a possible diagnostic result.
Increasing context also changes the reference configuration. This is a new
baseline version, not a rescoring of V2 or evidence to pool with it.

The [answer-free preflight](../evidence/calibration/model-serving-v3-preflight-20261008.json)
records the local preparation. To reproduce it in a fresh private directory:

```sh
.venv/bin/python -m agent_collab_evals prepare-serving-calibration \
  --recipe config/calibration/model-serving-v3.json \
  --state-root .private/serving-calibration --run-id calibration-v3-new
.venv/bin/python -m agent_collab_evals check-calibration-context \
  --root .private/serving-calibration/calibration-v3-new --fetch-tokenizer
```

Next: commit and deploy a clean build, regenerate private preparation and
context evidence there, reconcile the existing cumulative journal against
attributable billing evidence, and obtain fresh two-job approval. The previous
control's consumed authority cannot be reused. Held Modal allowances leave
only $0.18674552 under the $29 cap, so this diagnostic is not yet funded.
Do not release historical reserves without evidence or reset the journal.
If the diagnostic supports the change, seek separate approval for the complete
three-repetition qualification series. Do not proceed directly to a peer run.

The pinned-version [vLLM reproducibility documentation](https://docs.vllm.ai/en/v0.21.0/usage/reproducibility/)
does not guarantee default serving reproducibility. Identical request seeds
therefore do not justify collapsing the repetitions or assuming equal answers.
The [Qwen model card](https://huggingface.co/Qwen/Qwen3-4B#best-practices)
also recommends substantially more output space for reasoning. Batch invariance
is a possible separate calibration experiment, not an unqualified fix: [it can
change serving performance](https://docs.vllm.ai/en/stable/features/batch_invariance/#implementation-details)
and must not be silently enabled in either arm.

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
  was retained locally before deployment. The later completed control is
  reported above; preparation alone is not remote conformance.
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
  merely because the controller stopped. These safeguards passed no-spend tests
  before the live control described above; they do not imply stock eligibility.
- The stock runner checkpoint `90bbc1c` is committed and deployed. All 47
  targeted VM tests pass with resource warnings treated as errors. At that
  preparation checkpoint, `stock-control-1007b` had seven frozen requests,
  zero grants, and zero dispatches. The journal had 98 receipts, and the earlier
  $20 cumulative Modal ceiling rejected the control before authority issuance.
  [Deployment record](../evidence/deployment/paired-stock-preparation-20261007.json)
  binds the clean build and preparation. This closes the stock-runner deployment
  step. The later approved batch and completed live control are recorded above;
  current reference eligibility still failed.

The no-spend configs differ only in `condition`:
`config/pilots/peer-isolated-no-spend-oci-v1.json` and
`config/pilots/peer-collab-no-spend-oci-v1.json`. Their dollar figures are synthetic
test limits, not proposed paid allowances. `peer-pilot` cannot authorize live
execution. OCI execution requires explicit host runtime dependencies.

## Next, before any paid peer comparison

1. Retain the completed solo billing settlement when attributable provider
   evidence is available. Never release historical reserves without it.
   No new journal or implicit cap reset. The tested paired composition is
   committed and separately deployed, including the stock runner.
2. Retain the completed restricted Modal wrapper's live execution evidence.
   The new public/correctness/quality/performance
   factories share the declared same-GPU policy; the old solo factories remain
   exact-driver. Never silently mix those paths. All seven hidden stock jobs
   executed remotely and raw replay matches. This positive-path evidence is
   not a claim of comprehensive adversarial sandbox conformance.
3. Check the stock reference under the intended hidden correctness/quality
   gates, including a clean reference-versus-reference control where relevant.
   The retained calibration is independently replayed, but its correctness
   workload and build do not fully match the next pilot. The current live hidden
   stock control completed but failed eligibility for the reasons above.
   The V3 implementation and local preflight are complete; its two-job live
   diagnostic and complete qualification series remain pending.
   Preserve the old outcome;
   any calibration change creates a new version, not a favorable rescoring.
4. Extend request-bound dollar authority to the matched peer composition.
   The stock runner implements it for the exact seven-job control. Its approved
   spending scope is consumed, with all seven requests complete.
   The peer command still cannot authorize paid execution.
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
when checked on October 7, 2026. Reverify rates and provider routing before
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

The fresh VM preparation is `stock-control-1007b`, pinned to clean deployed
commit `90bbc1c`. Its proposal is explicitly unauthorized. Keeping all old
reserves, the control requires a cumulative Modal ceiling of at least
$28.81325448; the proposed rounded ceiling is $29. OpenRouter remains $3.10,
and this stock control makes no model API calls. The approved batch has now
been admitted. The original journal has 99 receipts; none of its historical
allowances were released. The separate workspace usage limit is $20, confirmed
by the user. Current provider billing settlement remains pending.
If the control fails its gates, report the negative result and revisit the
scenario version before launching the peer comparison.

The proposed actor sandbox lifetime must cover the entire sequential evaluation
schedule, including provider startup and collection. The offline check computes
48,600 seconds for four actors at the current 900-second agent deadline.
Neither the old sandbox candidate nor an arbitrary timeout edit qualifies that
deployment. A matching pinned development profile and conformance check remain
required. Do not raise caps, issue allowances, or run either stage until the
run-bound authority path and deployment checks pass and the user approves it.
