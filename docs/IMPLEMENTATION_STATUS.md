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
- Multi-actor delivery submits every top-level session concurrently and joins
  results in stable actor order. A partial failure remains safely retryable
  because each harness delivery is idempotent by job and materials digest.
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
  oversized-result transport failures; both invalid attempts remain preserved.
  The corrected series then completed three valid repetitions with all 27
  points eligible and a 1,001,872 ppm median score. This verifies the
  non-reference path and expected score direction, but its conservative
  candidate-min/reference-max bound is -798 ppm, so the stream tweak is not
  claimed as a reliably faster implementation.
- A digest-verified evaluator-private Qwen quality workload is materialized
  from MMLU, GSM8K, two BBH reasoning tasks and private structured transforms.
  Its 64 cases are balanced across thinking/non-thinking modes and objective
  scorers; the selection seed and answers remain outside the repository. A
  first diagnostic run exposed literal-placeholder prompting, truncation and
  oversized metadata transport rather than a model-quality result. The
  versioned V2 profile corrects those defects, pins eight-way request batching
  and reconstructs full receipts from a small durable-evidence pointer.
- Three paired reference/clean-control quality repetitions completed without
  retries. The frozen quality policy uses aggregate and per-family margins plus
  one-sided 95% stratified paired case-cluster bounds. Its executable scorer
  reproduces the 168/192 reference and 166/192 control outcomes, verifies all
  six receipt digests, and admits the clean control without exposing private
  prompts or answers.
- Raw serving evidence now targets an evaluator-owned Modal v2 Volume. A
  restricted non-GPU function successfully wrote and synced a probe which the
  trusted client read back by digest. Detached dispatch records make long calls
  reconnectable, and large raw bundles no longer depend on Modal's function
  result channel.
- Three clean-build formal stock-reference repetitions completed without
  retries: 672/672 requests and 27/27 raw artifacts validated under identical
  manifests, package set and GPU identity. The calibration ledger records
  provenance, variability and provisional bucket-specific TTFT/TPOT SLOs.
- The stock-OpenCode portion of ADR 0001 passed at exact OpenCode and SDK
  1.18.19. The zero-spend conformance probe proved provider-endpoint routing,
  durable session/message resume, a real stock `task` child session, child
  event observation, unchanged effective-surface digests under out-of-process
  observation, and actual removal of `task` from solo model requests.
- A real `OpenCodeHarnessRuntime` now implements the provider-neutral port
  through a small JSON-lines SDK bridge. It runs one isolated OpenCode state
  namespace per top-level actor, inherits no ambient host credentials, requires
  an opaque revocable gateway token issued per session, derives workspaces and
  state paths server-side, redacts the token from evidence, and rejects
  runtime-profile or effective-surface changes across resume. Event snapshots
  use monotonic cursors, bounded-buffer loss detection, terminal session/message
  reconciliation and a quiescent checkpoint barrier. A stream error, cursor
  gap, reconciliation gap or bridge timeout fails closed. Its two-job
  process-style restart test passes against a deterministic local gateway.
- The real adapter requires the pinned peer profile and gateway together for
  both peer conditions. A missing or mismatched integration fails closed,
  preventing treatment and control from silently collapsing.
- The peer profile pins MCP SDK 1.30.0, the sidecar implementation, dependency
  lock and five collaboration operations. Each OpenCode actor receives a
  revocable session-scoped sidecar credential that is activated only after the
  server session exists and is omitted from snapshots and model-visible tool
  arguments. The bridge waits for MCP readiness before recording its effective
  surface.
- A real four-actor test passes for both peer arms. Configuration, model, tool,
  permission and agent digests match within and across arms; actor-local paths
  are normalized explicitly. Private actors see only their own entries, shared
  actors see peer entries through the same calls, and shared state survives
  suspension, runtime replacement and a second job. Audit export confirms that
  only the shared arm contains cross-actor reads.
- The minimal collaboration substrate now implements durable SQLite-backed
  actor-private and organisation-shared twin modes behind `CollaborationBackend`.
  It derives identity from an object-bound session transport, enforces
  visibility server-side, signs actor- and query-bound cursors, supports
  idempotent publish/reply/search/notification operations, survives adapter
  restart and exports exact read/write audit evidence. Four-actor private and
  shared conformance tests pass against the same implementation.
- Independent storage and publication adapters now keep immutable artifact
  bytes owner-only and persist opaque prepared/bound/aborted publication state.
  `ArtifactService` proves ownership, joins a publication to an idempotent
  collaboration entry and materializes it only after checking the bound entry
  and recorded audience. Trusted storage reads require a service identity plus
  a purpose-bound, one-use authorization. Tests reject raw peer reads,
  guessed, unbound, aborted, cross-campaign and wrong-audience publications,
  forged service identities, mismatched purposes and authorization replay.
- A provider-neutral `BudgetAccount` port and durable SQLite adapter now enforce
  fixed organisation and actor model allocations using integer USD
  nanodollars. Reservations are atomic and conservative; exact settlement,
  rejection, release, ambiguous-outcome forfeiture, overrun detection, raw
  receipts, unit rates and identity metadata survive restart.
- A loopback OpenAI-compatible model gateway now implements the OpenCode token
  issuer lifecycle. Tokens are pending until bound to the actual runtime
  session and revoked with it. The gateway overwrites model, provider and
  inference routing from a transitively pinned profile, ignores caller identity
  headers, rejects requests before upstream execution when funds are
  insufficient, and charges a full reservation when usage or route identity is
  invalid.
- Stock OpenCode completed an end-to-end job through this gateway and a
  deterministic fake upstream. The conformance profile and rates are explicitly
  synthetic, so this proves local enforcement without making a pricing or live-
  route claim.
- A dependency-free OpenRouter upstream adapter pins the profile endpoint,
  keeps the API credential inside the gateway process, streams bytes without
  buffering the full response in the transport, and fetches the correlated
  generation record with bounded retries. Deterministic tests prove exact
  stream/metadata correlation, canonical model and provider attestation,
  native-token accounting, raw receipt persistence and authoritative
  provider-cost settlement without external spend.
- The independently pinned development billing catalog records the public
  DeepInfra FP8 prices and ZDR eligibility observed on 2026-08-21. One bounded
  end-to-end gateway canary then attested the expected provider and both model
  identities, retained the correlated metadata receipt and charged its exact
  $0.00001136 provider total. An initial successful check that retained only
  digests remains disclosed as an invalid evidence-lifecycle attempt; the
  corrected run preserves owner-readable raw receipts.

## Not implemented

The following remain gates, not implied capabilities:

1. Formal provider-selection evidence and condition-matched route
   qualification. The timestamped development catalog and one live gateway
   canary now pass, but they do not replace the frozen representative workload
   or registered provider-selection record.
2. Submission, compute and research services, plus filesystem-safe workspace
   snapshot/materialization and campaign-level storage sealing beyond the
   byte-level artifact path proved by the current spike.
3. Evaluator-owned untrusted candidate launch, public result release,
   remaining correctness/stability/shortcut gates, neutral selection, and a
   confirmatory registered score policy. The calibration Volume path is
   durable but is not yet the complete confirmatory evidence service or
   retention policy.
4. Four-condition scheduling, registered manifests, combined platform audit
   export and the
   preregistered statistical analysis.
5. An experiment-grade delivery outbox/receipt transaction. The development
   controller records a completed delivery after the harness call; the future
   authoritative ledger must atomically bind admission, runtime receipt and
   campaign state so a ledger-write failure cannot leave unaudited work.

## Next implementation gate

The baseline executor, offline replay and calibration scoring profile are now
complete and recorded in the
[calibration ledger](calibration/MODEL_SERVING_V0.md). The profile is pinned
for candidate-sensitivity work, but is not presented as a preregistered
confirmatory policy.

The direct-vLLM sensitivity gate and the paired generation-quality calibration
are complete. Raw calibration evidence is durably mirrored to the
evaluator-owned Modal Volume. Confirmatory retention, untrusted launch, and the
remaining hidden gates remain later platform-service work.

ADR 0001 is complete, and the model budget gateway's local enforcement and
provider-transport proofs now pass, including one bounded live development
canary. The next gate is provider-route qualification and its selection record,
followed by sandbox enforcement and the remaining compute, submission and
evaluator services. No live multi-condition model run is authorized yet.
