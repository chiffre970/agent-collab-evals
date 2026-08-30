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
- A write-once evaluator-private bundle now combines the frozen quality
  workload with disjoint hidden correctness requests, a separately seeded
  performance profile and integer-unit quality request specifications. Loading
  verifies owner-only modes, fixed resource names, every content digest, public
  disjointness and the registered bundle-manifest digest. This authority does
  not replace the pending registered filesystem sandbox.
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
- Campaign closure now requires a budget reconciliation gate. Harness stop
  revokes model tokens through an in-flight request barrier, then the controller
  verifies durable reservation and charge counters against an immutable
  out-of-ledger `BudgetPlan` and an independent raw-receipt verifier. Registered
  plans require their resolved-run-manifest digest at load time. Reconciliation
  rejects coherent rewrites of stored limits, usage, charges, counters and
  terminal audit when they differ from the plan or unchanged provider bytes.
  Active reservations, forfeitures, overruns, missing or invalid receipts and
  ledger inconsistencies emit `campaign.invalid` and cannot produce a
  `CampaignResult`. Local no-model tests must provide an explicit
  `no_model_calls` attestation.
- A frozen 2026-08-21 OpenRouter ZDR candidate snapshot and exact-decimal
  selection policy choose DeepInfra FP8 as the lowest projected-cost eligible
  route for the declared 100,000-input/10,000-output-token mix. The raw endpoint
  and authenticated ZDR responses are retained with file and content digests;
  the normalized candidate list is deterministically re-extracted on load. The
  committed development record binds the sources, candidate, policy and gateway
  digests.
- The selected route passed three live bounded probes for exact provider/model
  identity, visible text, forced tool calling, raw receipt capture and budget
  reconciliation. Two byte-identical text requests and the tool request all
  reported zero cached input tokens. The retained qualification cost $0.000052.
  Its exact record and all six raw receipts are repository-retained. Loading the
  selected route resolves their digests and independently replays each raw pair
  to reproduce identity, usage, cost and the exact $0.000052 total. An
  append-only development-attempt index also preserves the two adjacent
  diagnostic attempts: the three new records total $0.00015276; including the
  earlier superseded attempt, indexed route-qualification spend is $0.00020764.
- The OpenRouter transport now explicitly disables its response cache, and the
  selected endpoint snapshot attests that DeepInfra does not provide implicit
  caching for this model. ZDR membership alone is not treated as cache-isolation
  evidence because ZDR endpoints can still use implicit caching.
- OpenCode now launches through a separately pinned macOS `sandbox-exec`
  development adapter. Its kernel-level conformance test permits all loopback
  services and denies the host's nonloopback interface. Direct model-provider
  endpoints fail admission, no ambient credential enters the process, and the
  profile digest persists in runtime snapshots and across resume. This adapter
  does not isolate the gateway from other loopback services or enforce
  filesystem and process-resource limits.
- A provider-neutral candidate lifecycle now runs without external spend. The
  durable SQLite submission registry derives campaign and actor from the
  session transport, verifies artifact ownership, applies fixed per-actor
  candidate limits and keeps candidate existence and results owner-private.
  Admission uses a recoverable provisional record followed by an idempotent
  artifact-bound compute reservation, so interruption and concurrent registry
  retries converge without a cross-database atomicity claim.
- A separate durable compute ledger pins actor allocations that exactly
  partition the organisation GPU-second limit and a distinct hidden-evaluator
  allowance. Reservations are idempotent and artifact-bound, cannot borrow
  across actors, survive restart and expose no queue position or peer state.
  Completed public results remain pending until an explicit actor release
  boundary. Submission closure rejects pending, orphaned or mismatched compute
  reservations.
- The fake model-serving evaluator validates real candidate manifests through
  the campaign contract while returning pinned integer fixtures through a
  separate durable evaluator-owned receipt ledger. The registry stores opaque
  evaluator receipts, recomputes and persists deterministic reference-aware
  selection after closure, and exposes an opaque selection receipt. The system
  reference wins ties. Hidden evaluation revalidates the authoritative
  selection and evaluates either the winning candidate or registered reference
  artifact under the separate evaluator allowance. It never reuses the visible
  score and is never agent-visible.
- Artifact ingestion and materialization now include race-resistant single-file
  workspace operations rooted in the authenticated session's server-side
  workspace assignment. Agents cannot nominate another root. Directory
  traversal, symlink traversal and overwrite fail closed. Final storage sealing
  rechecks every blob digest, binds the final selection/evaluation manifest and
  prevents later artifact admission.
- `collab-evals fake-candidate-lifecycle` executes two owned candidates through
  admission, public evaluation, release, selection, hidden evaluation and
  storage sealing. It uses no GPU or model API.
- A provider-neutral `ComputeBackend` execution contract now separates durable
  orchestration, external dispatch and evaluator-owned evidence resolution.
  Its SQLite adapter records dispatch intent before the side effect, permits
  only one dispatcher across processes, retains pending work across collection
  timeouts, refuses to redispatch an ambiguous outcome and revalidates terminal
  evidence during close-time reconciliation.
- A pinned development transport composes this contract with the existing
  Modal/vLLM evaluator. It launches one declared candidate repetition in a
  single-use L4 function, passes only a minimal Modal CLI environment and binds
  the campaign, candidate, Modal client, evaluator script and evidence Volume
  to digests. A visible-only evaluator maps the resulting performance score and
  measured function-body use into the existing candidate lifecycle. Tests use
  a fake transport and local digest-verified evidence, so they incur no GPU
  spend.
- Candidate manifests no longer contain executable argv. The V0 schema accepts
  only typed, bounded, allowlisted vLLM settings, and the evaluator constructs
  the fixed command. Scored GPU functions receive no secret, block external
  networking, mount the populated model cache read-only and do not mount the
  durable evaluator evidence Volume. Each invocation sees only its
  evaluator-issued staging subpath. After the candidate process stops, the
  evaluator syncs complete raw evidence there and returns a small digest
  pointer. A trusted collector verifies the staged bytes, and a separate
  trusted function copies them to durable evaluator storage.
- Concurrent evaluation callers treat registered, dispatching and dispatched
  work as nonterminal. An in-progress observation leaves the candidate and its
  reservation untouched. Dispatch evidence is independently resolved and
  checked during collection, resolution and reconciliation. Observed execution
  time is not clamped, so an overrun remains visible and invalidates closure.
- The Modal transport now requires an unused request- and profile-bound spend
  authorization before dispatch. A separate SQLite authorization service
  durably issues and atomically consumes the authorization against the frozen
  run manifest, transport profile, request digest and approval evidence.
- Compute reconciliation now reconstructs exact requests from a canonical,
  digest-bound, write-once run manifest after restart. Ledger rows bind the
  manifest digest, and closure rejects missing or extra planned executions.
  No request replay into process memory is required.
- Campaign closure requires a separate compute reconciliation gate in addition
  to budget reconciliation. The no-compute adapter accepts only a frozen run
  manifest that disables compute and has no profiles or requests.
- The full local suite passes with `ResourceWarning` reporting enabled; all
  SQLite connections opened by tests and adapters are explicitly closed.
- The bounded live hardened-function conformance passed on 2026-08-29. The
  scored Modal L4 function used the fixed revision-addressed local model path,
  blocked external networking, mounted the model cache read-only, received no
  secret and had no evidence-volume mount. Separate trusted persistence and
  digest-resolved collection also passed. The calibration ledger records the
  app, evidence root and receipt digests.
- The first durable full-reference dispatch exposed and then closed two
  integration failures without admitting a score. The transport now detaches
  the ephemeral Modal app around its spawned function and can reconcile a
  terminal infrastructure failure without success-only scoring evidence. A
  second run completed the benchmark but proved that restricted functions
  cannot use Modal's large-result blob upload. A CPU-only 4 MiB conformance now
  proves the replacement isolated-staging and trusted-copy path end to end.
- The replacement full-reference run completed all nine points through durable
  dispatch, isolated staging, trusted persistence, normalization, and terminal
  reconciliation. The frozen manifest reconstructed exactly one execution;
  dispatch and evidence digests re-resolved after restart. Terminal collection
  now invokes this reconciliation gate automatically.
- The benchmark runner no longer relies on an implicit public workload. The
  compute profile pins the performance-profile path and digest, passes that
  exact profile to the trusted runner and verifies its identity in dispatch,
  remote, normalized and reconciliation evidence. The changed script passed a
  new bounded live conformance check on 2026-08-30 with all six declared
  isolation and persistence controls true.
- A split-scope evaluator facade now binds one registered evaluator identity to
  distinct visible and hidden evaluator profiles, workloads, compute accounts,
  schedules and evidence namespaces. Durable outer receipts bind the selected
  lane and underlying evaluator authority; cross-scope use and ledger tampering
  fail closed.
- A hidden serving evaluator now composes correctness, reference-relative
  quality and performance behind one durable receipt. Its fixed phase
  allowances exactly partition the hidden reservation; it derives stable phase
  reservations, re-resolves all evaluator-owned evidence after restart, sums
  uncapped usage and admits the performance criterion only when every gate
  passes. No-spend tests cover retries, restart, failed gates, wrong scope,
  budget mismatch and composite or phase-ledger tampering. The underlying
  registered Modal phase adapters remain to be promoted.
- A provider-neutral trusted correctness scorer now loads both public and
  hidden JSON-lines workloads, constructs fixed non-thinking OpenAI-compatible
  requests and scores verbatim response bytes. It binds every response digest,
  checks the served-model identity, single stopped assistant choice and
  exact/regex/case-folded result, and distinguishes schema from answer failures
  without copying expected hidden answers into diagnostics. Hidden bundle
  validation reuses this parser so materialization and scoring cannot accept
  different request contracts.
- A paired hidden-quality evaluator now enforces the frozen three-repetition
  reference-relative design. It freezes a registered within-pair role order,
  derives separate artifact-bound reference and candidate reservations, stores
  six opaque backend receipts and replays the paired bootstrap policy after
  restart. Its portable authority digest binds parsed policy values without
  host paths. It sums uncapped usage and rejects schedule, budget, scope,
  policy, artifact or ledger changes. The existing calibration reference
  receipts cannot be substituted for contemporaneous hidden runs.
- A provider-neutral quality-repetition adapter now maps every paired run to
  the durable `ComputeBackend` contract. It validates candidate manifests,
  binds role and repetition into the hidden execution request, pins the compute
  backend profile and accepts only normalized evidence carrying the exact
  campaign, hidden bundle, quality profile, workload, candidate and repetition
  identity. Retry and restart recover the same execution and opaque receipt;
  accounting uses the backend's uncapped duration. An integration test now runs
  this adapter through the real `SqliteComputeBackend`, its frozen run manifest,
  fixed standard evidence envelope, dispatch-evidence verification and
  close-time reconciliation rather than relying only on a permissive fake.
- A Modal quality transport now binds each hidden repetition to the frozen
  campaign, hidden-workload manifest, private workload, canonical request file,
  candidate, role and repetition before dispatch. It reuses the hardened
  single-use L4 quality runner, requires durable spend authorization and
  normalizes the retained Modal bundle into the standard compute evidence
  envelope. A no-spend integration test composes that envelope with
  `SqliteComputeBackend` and the quality-repetition adapter, accounts for the
  uncapped measured duration and completes close-time reconciliation. Tests now build the
  production profile from a real 64-case private bundle, compose the actual
  transport with durable SQLite spend authorization, and execute the complete
  three-pair role schedule as six separately reconciled durable compute jobs.
  The `modal_quality_compute.py` preflight reconstructs the same authority from
  a registered hidden-bundle digest and requires an explicit approval reference
  before it can dispatch one billable conformance repetition.
- The bounded quality-compute conformance completed on 2026-08-30 with one
  spend-authorized reference repetition. It retained all 64 raw responses,
  admitted a valid 859,375 ppm quality result, recorded 574 uncapped seconds,
  and reconciled the sole execution from its frozen manifest. The first
  collection exposed a transient empty-file response from Modal Volume staging;
  the collector now retries missing, empty, and digest-incomplete visibility.
  Recovery reused the original function call and did not dispatch another GPU
  job. The exact registered-study profile remains pending.
- Provider-neutral hidden correctness and performance evaluators now map one
  phase to the durable `ComputeBackend`, accept only the standard evidence
  envelope, bind the campaign, hidden bundle, workload and candidate digests,
  and use uncapped backend time for accounting. The hidden-performance adapter
  reuses the pinned benchmark transport with the evaluator-private workload
  profile and translates retained benchmark evidence without changing the
  execution boundary.
- The hidden-correctness Modal adapter builds its production profile from the
  real private bundle, requires durable request-bound spend authorization and
  uses the same restricted, secret-free, read-only-model-cache serving
  function as quality. Only prompts and generation settings enter the scored
  function. The trusted collector keeps expected checks local, rescoring the
  retained raw response bytes and rejecting any disagreement with the stored
  result. No-spend tests cover the real profile factory, exact Modal command,
  durable authorization, compute reconciliation and raw-evidence replay.
- A no-spend integration now executes the complete hidden evaluator through
  the real phase adapters: one correctness job, six paired quality jobs and one
  hidden-performance job. The composite admits only after all three phases
  resolve, reports the hidden performance criterion, sums 81 uncapped simulated
  seconds and independently reconciles all eight planned compute executions.
- The `modal_hidden_phase_compute.py` preflight provides one fail-closed
  operator path for the remaining bounded live qualifications. It accepts only
  correctness or performance, resolves a digest-pinned private bundle, creates
  the phase-specific Modal and evaluator profiles, freezes the sole compute
  request, requires a durable approval-bound spend authorization, and reports
  a result only after terminal compute reconciliation. Adding the command does
  not authorize or perform a billable run.
- A provider-neutral randomized-block scheduler now materializes complete
  four-condition blocks with one run per condition. Its versioned SHA-256
  assignment algorithm binds conditions to predeclared execution positions,
  derives run and actor stochastic seeds from position rather than treatment,
  and preserves one task seed and material digest across the block. Plans and
  mechanically resolved run manifests are canonical, content-addressed,
  write-once and fsynced. Loading recomputes the complete assignment from the
  registered inputs, so label, order or seed tampering fails closed.

## Not implemented

The following remain gates, not implied capabilities:

1. Registered-study promotion remains separate from the passing development
   route, cache and macOS sandbox evidence. Before scored runs, freeze registered
   copies of the provider snapshot, selection policy and record, billing and
   gateway profiles, immutable budget plan, receipt-verifier profile, deployment
   build and block-validity rule. Replace or layer
   the current development network policy with gateway-specific local-service,
   filesystem and process-resource enforcement. The target environment needs a
   pinned kernel- or container-level adapter and equivalent conformance proof.
2. Promotion of the development Modal compute adapter to a registered backend,
   including the final container and capability policy, fixed actor-slot
   scheduler integration and registered evidence retention. The bounded live
   boundary proof now passes; durable run authority, spend authorization and
   mandatory compute reconciliation pass locally. The current adapter accepts
   only validated declarative serving candidates; it does not execute arbitrary
   candidate-supplied commands. The research broker is also not implemented.
3. Live qualification and registered promotion of the implemented hidden
   correctness and performance adapters, plus stability and shortcut gates,
   efficient registered phase co-scheduling, and complete confirmatory evidence
   retention. The paired-quality transport passes locally through the durable
   compute boundary and has passed one bounded live reference conformance. The
   correctness orchestration added to the pinned runner changes its script
   digest, so the shared hardened boundary must be requalified before a scored
   run. All three phase profiles still need registered copies. Neutral
   selection, result-release enforcement, hidden-workload authority and
   fail-closed outcome composition pass locally.
4. The complete registered study manifest and composition root, execution of
   the implemented four-condition schedule, combined platform audit export and
   the preregistered statistical analysis. Block assignment and per-run
   resolution now pass locally; they are not yet wired to campaign execution.
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
evaluator-owned Modal Volume. The durable development compute state machine,
frozen run authority, durable spend authorization, pinned Modal transport, and
visible evaluator composition are implemented and tested without live spend.
The hardened command, secret, network, model-cache and evidence boundaries
passed a bounded live Modal conformance run. The development reference,
quality, correctness and hidden-performance paths now compose locally through
durable execution. Because correctness changed the shared runner digest, one
bounded live requalification remains a deliberate, separately authorized
check. Confirmatory retention remains later platform-service work.

ADR 0001 and the fake submission/compute/evaluator slice are complete.
Development provider-route qualification, condition-matched cache isolation,
nonloopback egress denial and post-stream budget reconciliation also pass.
Gateway-specific loopback isolation, filesystem, and process-resource
enforcement remain scored-run gates. The durable development dispatch and
collection gate is complete. Hidden workload separation and the no-spend
three-phase composition through all real phase adapters are also complete. The
paired-quality Modal path passes both the no-spend durable integration gate and
one bounded live reference conformance. The next gates are bounded live
correctness/performance qualification, frozen registered profiles, complete
study composition and four-condition campaign execution. Resolved-run
assignment is now implemented, but the full registered configuration authority
is not. No live multi-condition model run is authorized yet.
