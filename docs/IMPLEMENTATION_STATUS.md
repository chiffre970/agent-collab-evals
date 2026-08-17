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
  arrays without invoking a shell. Bucket-specific goodput SLOs are explicit
  positive integer milliseconds and are passed directly to pinned vLLM.
- The transitively pinned calibration measurement profile separates warm
  steady-state scoring from startup, declares three fresh-container
  repetitions, fixed warmups and point order, whole-repetition retries,
  environment matching, timing authority and evaluator-private persistence.
- Pinned vLLM result parsing verifies point identity, counts, detailed token
  totals and derived throughput, rejects duplicate keys and non-finite values,
  and normalizes scored floats to integer units while retaining the exact raw
  bytes and their digests.
- The transitively pinned calibration scoring profile requires zero request
  failures and at least 90% joint TTFT/TPOT attainment at all nine points. It
  scores the highest offered rate in each bucket, equally averages each
  bucket's ratio to its reference median, takes the median of three candidate
  repetitions and reports a conservative candidate-min/reference-max lower
  measurement bound. New candidate evidence must contain vLLM's direct
  in-memory goodput result.
- The legacy stock baseline was replayed from all 27 detailed raw results with
  a 0.1 ms ambiguity guard. All requests were safely classifiable and the
  replay reproduced the three pinned reference scalar values exactly.
- Local measurement bundles are committed atomically per repetition and
  attempt, are immutable after commit and detect changed or corrupted raw
  results on load.
- The OpenRouter development preflight reads only its API credential from the
  environment. Model identity, endpoint, provider routing, fallback/privacy
  policy and inference settings live in a validated, digest-recorded committed
  profile. The default DeepInfra profile passed exact model/provider attestation,
  ZDR routing and a live canary; alternate committed profiles are selected by
  an explicit CLI path rather than an environment variable. Registered studies
  will use separately frozen profiles and a recorded pre-outcome provider
  selection rule.
- The Modal vLLM measurement adapter is private, requests one L4, mounts only the
  Hugging Face secret it needs, disables retries, limits runtime to 1,800
  seconds and records non-secret model, runtime, GPU, canary and resolved
  package-set metadata. Its full path runs one complete reference or declared
  candidate repetition in a
  fresh single-use container, persists partial failure evidence and never uses
  provider timing as the serving score.
- A legitimate candidate-sensitivity artifact is prepared using vLLM's
  documented `stream_interval=10` setting. It holds model, engine, image and
  hardware fixed. Initial calibration attempts exposed client-lifetime and
  oversized-result transport failures; both invalid attempts remain preserved
  and no candidate performance result has been admitted from them.
- A digest-verified evaluator-private Qwen quality workload is materialized
  from MMLU, GSM8K, two BBH reasoning tasks and private structured transforms.
  Its 64 cases are balanced across thinking/non-thinking modes and objective
  scorers; the selection seed and answers remain outside the repository.
- Raw serving evidence now targets an evaluator-owned Modal v2 Volume. A
  restricted non-GPU function successfully wrote and synced a probe which the
  trusted client read back by digest. Detached dispatch records make long calls
  reconnectable, and large raw bundles no longer depend on Modal's function
  result channel.
- Three clean-build formal stock-reference repetitions completed without
  retries: 672/672 requests and 27/27 raw artifacts validated under identical
  manifests, package set and GPU identity. The calibration ledger records
  provenance, variability and provisional bucket-specific TTFT/TPOT SLOs.

## Not implemented

The following remain gates, not implied capabilities:

1. The ADR 0001 stock-OpenCode feasibility spike and a real
   `HarnessRuntime` adapter.
2. Provider-gateway dollar enforcement, formal provider-selection evidence and
   condition-matched OpenCode model routing.
3. Collaboration, publication authorization, artifact storage, submission,
   compute and research services.
4. Evaluator-owned untrusted candidate launch, public result release,
   confirmatory registered score policy, calibrated hidden gates and neutral
   selection. The calibration Volume path is durable but is not yet the
   complete confirmatory evidence service or retention policy.
5. Four-condition scheduling, registered manifests, audit export and the
   preregistered statistical analysis.

## Next implementation gate

The baseline executor, offline replay and calibration scoring profile are now
complete and recorded in the
[calibration ledger](calibration/MODEL_SERVING_V0.md). The profile is pinned
for candidate-sensitivity work, but is not presented as a preregistered
confirmatory policy.

The next evaluator gate is to run at least one legitimate non-reference
candidate through direct vLLM goodput and verify that the score responds in the
expected direction. The architecture-neutral held-out generation interface and
private 64-case workload now exist; three paired reference/clean-control runs
must still freeze the downstream non-inferiority margins before agents optimize
against a confirmatory evaluator. Raw calibration evidence is durably mirrored
to the evaluator-owned Modal Volume, while confirmatory retention and export
remain a later platform-service gate.

The OpenCode conformance spike can proceed independently of evaluator
calibration. Both must pass before broader V0 implementation assumes OpenCode
can provide durable sessions, native handoffs, non-mutating observation and a
matched peer-tool surface around an enforceable serving campaign.
