# Implementation status

The current code is a scenario-shaped calibration slice. It establishes the
smallest useful seams before adding the runtime and collaboration machinery.

## Implemented

- Core campaign lifecycle types and narrow `HarnessRuntime`, `EventSink` and
  `CampaignSnapshotStore` ports have no dependency on OpenCode, Modal or the
  task family.
- The fake harness preserves actor, session and delivered-job identity across
  a serialized process-style resume. Local snapshots use atomic replacement;
  local events are append-only, fsynced and sequence checked.
- `model_serving_v0` transitively hashes its mission, schemas, workloads,
  hidden evaluator interface and reference candidate. Candidate validation
  rejects unknown fields and changes to the fixed model revision or hardware.
- The reference candidate pins Qwen/Qwen3-4B commit
  `1cfa9a7208912126459214e8b04321603b3df60c`, one L4, vLLM 0.21.0, the CUDA
  base image, API paths, served model name and generation-config policy.
- The public workload expands into nine deterministic vLLM benchmark argv
  arrays without invoking a shell. Optional goodput SLOs are explicit positive
  integer milliseconds and remain unset until calibration.
- The transitively pinned calibration measurement profile separates warm
  steady-state scoring from startup, declares three fresh-container
  repetitions, fixed warmups and point order, whole-repetition retries,
  environment matching, timing authority and evaluator-private persistence.
- Pinned vLLM result parsing verifies point identity, counts, detailed token
  totals and derived throughput, rejects duplicate keys and non-finite values,
  and normalizes scored floats to integer units while retaining the exact raw
  bytes and their digests.
- Local measurement bundles are committed atomically per repetition and
  attempt, are immutable after commit and detect changed or corrupted raw
  results on load.
- The OpenRouter development preflight reads only its API credential from the
  environment. Model identity, endpoint, provider routing, fallback/privacy
  policy and inference settings live in a validated, digest-recorded committed
  profile; registered studies will use separately frozen profiles.
- The Modal reference adapter is private, requests one L4, mounts only the
  Hugging Face secret it needs, disables retries, limits runtime to 1,800
  seconds and records non-secret model, runtime, GPU, canary and resolved
  package-set metadata. Its baseline path runs one complete repetition in a
  fresh single-use container, persists partial failure evidence and never uses
  provider timing as the serving score.
- Three clean-build formal stock-reference repetitions completed without
  retries: 672/672 requests and 27/27 raw artifacts validated under identical
  manifests, package set and GPU identity. The calibration ledger records
  provenance, variability and provisional bucket-specific TTFT/TPOT SLOs.

## Not implemented

The following remain gates, not implied capabilities:

1. The ADR 0001 stock-OpenCode feasibility spike and a real
   `HarnessRuntime` adapter.
2. Provider-gateway dollar enforcement and condition-matched OpenCode model
   routing.
3. Collaboration, publication authorization, artifact storage, submission,
   compute and research services.
4. Evaluator-owned untrusted candidate launch, durable external evidence,
   public result release, registered SLO/scalar policy, hidden gates and neutral
   selection.
5. Four-condition scheduling, registered manifests, audit export and the
   preregistered statistical analysis.

## Next implementation gate

The baseline executor has completed one engineering pilot and three valid
formal repetitions, recorded in the
[calibration ledger](calibration/MODEL_SERVING_V0.md). The request-level result
is stable and the ledger proposes bucket-specific TTFT/TPOT SLOs, a 90% joint
attainment rule, three candidate repetitions and unchanged hard lifecycle
limits. Those conclusions have not been silently written into the source
measurement profile.

The next evaluator gate is to implement offline/direct vLLM goodput replay,
freeze the exact cross-bucket scalar and improvement bound in a new profile,
and validate score sensitivity with at least one legitimate non-reference
candidate. The raw evidence also needs a durable evaluator-owned backend before
confirmatory execution; the ignored local store is not sufficient retention.

The OpenCode conformance spike can proceed independently of evaluator
calibration. Both must pass before broader V0 implementation assumes OpenCode
can provide durable sessions, native handoffs, non-mutating observation and a
matched peer-tool surface around an enforceable serving campaign.
