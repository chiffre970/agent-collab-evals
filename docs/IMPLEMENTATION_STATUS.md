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
  The SQLite delivery outbox commits the canonical job and complete recipient
  set before fan-out, retains runtime-profile-bound acknowledgements, restores
  completed job state after restart and reconciles exact jobs, sessions,
  receipts and audit records before campaign closure. An observational event
  write failure cannot erase or repeat the authoritative delivery. After an
  uncertain restart, the OpenCode adapter reconciles the exact canonical prompt
  against durable session messages before it sends anything again.
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
- The process-sandbox port now receives an immutable server-derived launch
  context for every actor: the exact workspace, isolated runtime state,
  runtime-assets root and model endpoint. The OpenCode adapter constructs this
  context after creating the canonical paths. This is the minimum interface a
  registered container adapter needs to bind filesystem and broker policy; the
  development macOS adapter validates the endpoint but does not overclaim use
  of the path information.
- ADR 0002 selects a rootless Docker-compatible OCI boundary for registered V0
  agent processes. Its strict candidate profile and command builder require no
  network, dedicated session model and peer-tool Unix sockets, a read-only root, explicit
  read-only and actor-writable mounts, a bounded `noexec` temporary filesystem,
  no secrets, a nonroot user, dropped capabilities, `no-new-privileges`, and
  fixed CPU, memory, process-count, and lifetime limits. The launcher and
  bridge sources are digest-pinned. The adapter rejects the committed
  candidate because its runtime image and live conformance gates remain
  unresolved.
- The model and peer-tool gateways now optionally create one short Unix-domain
  socket per issued token and advertise their fixed loopback endpoints inside
  the OCI container. Each listener accepts only its own activated token; a
  valid token from another listener receives `403`. Revocation stops the
  listener and removes its socket. The dependency-free session launcher relays
  both fixed endpoints and enforces a process-tree timeout. Direct and relayed
  local conformance requests preserve the existing budget, receipt,
  collaboration, visibility, and identity authorities.
- A provider-neutral candidate lifecycle now runs without external spend. The
  durable SQLite submission registry derives campaign and actor from the
  session transport, verifies artifact ownership, applies fixed per-actor
  candidate limits and keeps candidate existence and results owner-private.
  Admission uses a recoverable provisional record followed by an idempotent
  artifact-bound compute reservation, so interruption and concurrent registry
  retries converge without a cross-database atomicity claim.
- A zero-spend real-adapter rehearsal now executes all four conditions against
  the pinned OpenCode runtime and deterministic in-process model. It delivers
  the same scenario and coordination-conformance jobs to every arm. Solo proves
  both coordination tools absent; native multiagent completes stock OpenCode
  task handoffs; both peer arms publish and read through the same peer-tool
  integration. The isolated arm records zero cross-actor reads, while the
  collaborative arm records cross-actor reads. Every run closes through the
  durable delivery, budget, compute, event, snapshot, and collaboration
  evidence paths and remains explicitly unscoreable.
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
  the real phase adapters: one correctness job, six paired quality jobs, and
  three hidden-performance jobs. The composite admits only after all three
  phases resolve, reports the aggregated hidden performance criterion, sums
  125 uncapped simulated seconds, and independently reconciles all 10 planned
  compute executions.
- The `modal_hidden_phase_compute.py` preflight provides one fail-closed
  operator path for bounded live qualifications. It accepts only
  correctness or performance, resolves a digest-pinned private bundle, creates
  the phase-specific Modal and evaluator profiles, freezes the sole compute
  request, requires a durable approval-bound spend authorization, and reports
  a result only after terminal compute reconciliation. Restart recovery
  distinguishes a new request from an already consumed authorization, so it
  can collect an existing call without redispatch or the original approval
  text. Adding the command does not authorize or perform a billable run.
- Bounded live correctness and performance executions completed from clean,
  pushed commit `69ea2d9` on 2026-09-01. Correctness passed all 8 private cases,
  retained and rechecked every response digest, accounted for 285 seconds and
  reconciled. Performance retained and rechecked all 9 benchmark points,
  accounted for 749 seconds and reconciled without redispatch after a local
  collection fix. Its reference score was correctly ineligible because the
  medium/1 and long/1 points missed the current joint-latency SLO. The adapter
  now distinguishes complete-but-ineligible benchmark evidence from a failed
  compute execution and uses its registered scope-specific evidence resolver.
  The calibration ledger records the exact calls and evidence digests.
- A pinned hidden-performance calibration plan requires three stable,
  independent stock-reference measurements. Its no-spend derivation tool
  revalidates raw vLLM detail and direct goodput, requires identical campaign,
  candidate, build,
  model, package and GPU provenance, applies the declared P95 headroom and
  rounding rule, verifies every point's joint attainment, and emits a
  write-once proposal rather than changing the active profile. The observed
  conformance result motivated but was excluded from derivation. Three fresh
  measurements completed from build `e8cbe91`; all 27 raw points and identical
  provenance passed validation. The resulting write-once proposal has digest
  `sha256:5b4ce9eb9aaa483e7c9c268436a79c5bb054d726d9f9178d5d7b4f78a12f86f4`.
  It changes TTFT gates to 250/500/2150 ms, retains TPOT gates at 45/60/90 ms,
  and remains explicitly unregistered. The calibration bundle is retired, and
  any scored study must materialize a fresh hidden seed after policy freeze.
- Calibration execution now uses three independent runner-repetition-1 calls,
  each assigned an outer calibration index. This avoids conditioning a later
  calibration allocation on whether an earlier reference passed the SLO being
  calibrated. Phase keys preserve the required `:performance` suffix. A
  sequential-repetition probe failed before GPU dispatch and is retained as
  invalid diagnostic evidence. The third valid call's trusted persistence
  function stopped after GPU completion; restart collection reused the same
  function call and authorization, then completed reconciliation without
  redispatch.
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
3. Registered promotion of the implemented hidden correctness, quality and
   performance adapters, plus stability and shortcut gates, efficient phase
   co-scheduling, and complete confirmatory evidence retention. All three
   adapters now pass bounded live execution and durable reconciliation. The
   replacement performance policy is frozen separately from public scoring,
   mechanically verified against its calibration proposal and injected into
   the runner and evidence boundary. A fresh post-freeze hidden bundle is
   materialized and digest-pinned in the composition candidate. Do not
   reinterpret or pool the retained ineligible calibration results. The hidden
   phase policy is now frozen and the performance phase aggregates three
   independent, durably receipted repetitions instead of accepting one result.
   Stability and prohibited-shortcut authorities remain missing. Neutral
   selection, result-release enforcement, hidden-workload authority and
   fail-closed outcome composition pass locally.
4. The complete registered study manifest and composition root, execution of
   the implemented four-condition schedule, combined platform audit export and
   the preregistered statistical analysis. A strict composition candidate pins
   every currently available profile, the calibration lineage and the fresh
   hidden workload identity. It always rejects scored execution and names the
   missing authorities. A separate no-spend authority now pins the source tree,
   composition candidate, block plan and explicit fake-runtime, no-model and
   no-compute policies. Its structural runner resolves and executes every
   four-condition campaign lifecycle, retains per-run evidence and verifies a
   combined canonical audit. This rehearsal is non-scoreable and does not
   exercise native handoffs or peer collaboration surfaces. A separate
   no-spend four-condition composition now exercises real OpenCode, the development
   sandbox, session gateway, budget ledger, delivery outbox and closure gates
   against an in-process deterministic stream. Its audit is replayed from the
   retained ledger, outbox, event log, compute authority and harness snapshot.
   The registered composition root and live agent-to-evaluator integration
   remain missing. Separate development candidate-tool wiring is described
   below; neither rehearsal is an optimization experiment.
5. Registered single-dispatcher binding for the delivery outbox. The durable
   outbox and idempotent harness receipts now close the earlier ledger-write and
   partial-fan-out gap. V0 still needs the registered composition to pin one
   campaign dispatcher. Multiple concurrent controller processes would require
   a separately designed cross-process claim or lease protocol.

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
durable execution. The shared runner and both remaining hidden phase adapters
have passed bounded live execution and reconciliation. Confirmatory retention
remains later platform-service work.

ADR 0001 and the fake submission/compute/evaluator slice are complete.
Development provider-route qualification, condition-matched cache isolation,
nonloopback egress denial and post-stream budget reconciliation also pass.
Gateway-specific loopback isolation, filesystem, and process-resource
enforcement remain scored-run gates. The durable development dispatch and
collection gate is complete. Hidden workload separation and the no-spend
three-phase composition through all real phase adapters are also complete. The
paired-quality Modal path passes both the no-spend durable integration gate and
one bounded live reference conformance; correctness and performance have now
done the same. The performance-calibration series and deterministic proposal
are complete. The separate hidden scoring input and fresh post-freeze hidden
bundle are complete. The bundle is retained without its seed on a dedicated,
write-once evaluator-private Modal Volume; a committed receipt binds complete
read-back verification. Registered hidden-phase, SQLite collaboration, and
deny-all research profiles now pass semantic validation. A fail-closed OCI
enforcement candidate, digest-pinned bridge and launcher, dedicated-session
Unix-socket gateways, and exact command builder now exist. Promotion still
requires a pinned runtime image and engine plus retained live conformance
evidence. The remaining implementation gates are registered provider, runtime,
compute, budget, enforcement, stability, shortcut, analysis, block-plan, and
platform-build authorities, followed by the executable composition root. Resolved-run
assignment is implemented, but the full registered configuration authority is
not. A one-block, four-condition structural rehearsal passes with zero model
calls and zero compute executions. Its authority and audit fail closed on
configuration, material, source and retained-evidence drift. It uses the fake
runtime and explicitly records that treatment surfaces were not exercised. A
second rehearsal now passes all four conditions through the real runtime and
control adapters with a deterministic in-process model, zero external model
calls, zero compute, exercised treatment surfaces, and independently reconciled
evidence. It remains unscoreable and uses the partial development sandbox. The
next concrete gates are native admission qualification and an image-backed OCI startup
and qualification run. The image command now forwards stdin, resolves the local
OpenCode executable, and packages the peer sidecar with a container-local path.
These changes are checked locally, but do not establish container conformance.
Then qualify the development candidate/evaluation path against live compute and
freeze the remaining registered authorities and executable composition root.
No live multi-condition model run is authorized yet.

## Review follow-up: September 5, 2026

Completed locally:

- Require disjoint workspace, runtime-state, and runtime-assets roots, with
  separate broker roots. The harness uses `scripts/runtime` as its assets root,
  not the repository root. Darwin remains explicitly network-only.
- Correct OCI stdin forwarding, executable lookup, and peer-sidecar packaging.
  The candidate remains execution-disabled with no pinned image.
- Roll back partially created organizations on startup failure. Shutdown
  attempts every actor's bridge and credential cleanup even when evidence
  capture fails, and propagates those failures. Bridge termination signals its
  dedicated process group while the bridge is running.
- Retain the task seed in rehearsal audit schema v2. Replay rematerializes the
  task and compares both its digest and complete jobs with the outbox.
- Retain exact synthetic model requests and runtime message transcripts.
  Replay matches request digests against budget records, derives tool calls
  from reconciled provider streams, and checks completed tools and child
  responses against the retained session tree. Summary counters are no longer
  accepted as independent proof.
- Add a real-OpenCode interruption test after prompt persistence but before
  outbox acknowledgement. Restart from the older snapshot must find exactly
  one canonical prompt and make no second model call. Missing pre-crash event
  history still rejects closure; recovery does not make that run scoreable.

Plan after the initial review fixes:

1. Implement and qualify native identity/concurrency admission at `N`. The
   runtime explicitly reports this limitation and rejects registered native
   execution; it does not claim that observation enforces the cap.
2. Build and pin the OCI image and engine, then qualify the deployment boundary.
   No container engine is installed in the current development environment.
3. Wire agent-owned candidate creation, submission, and visible evaluation into
   a solo end-to-end run with reconciled closure.
4. Complete the registered authorities and remaining evaluator gates before
   seeking approval for scored multi-condition pilots.

Validation: the default suite ran 276 tests (263 passed, 13 skipped). Six
additional enabled local integration tests passed, including the four-condition
rehearsal and interruption recovery. JavaScript syntax and whitespace checks
passed. No external model or GPU calls were made. OCI execution was not tested.

## Next four gates: implementation progress

1. **Native admission: development integration implemented.** The SQLite
   service reserves child slots before stock task dispatch and retains them
   across process restarts. A separately pinned before/after hook connects
   stock OpenCode to the service without transforming tasks. The real
   four-condition rehearsal passes with this integration in the native arm.
   Schema v3 replay additionally reconciles the native ledger with the runtime
   tree. Registered interception and containment qualification remain open.
2. **OCI qualification: blocked on deployment prerequisites.** The current
   host is macOS, neither Docker nor Podman is installed, and the image is not
   pinned. The gateway and OCI runtime need a Linux deployment. The new
   `readiness` command reports these facts and the registered authority gaps;
   it does not interpret engine availability as conformance evidence. The image
   recipe includes the candidate sidecar and native hook. Their service gateway
   supports per-capability Unix sockets, but OCI relays still need wiring before
   qualification.
3. **Solo candidate flow: no-spend end-to-end wiring implemented.** Real
   OpenCode submits a typed candidate through an MCP sidecar and session-bound
   service, requests public evaluation, and reads the result after a fixed
   controller-owned release boundary. The existing storage, submission,
   evaluator, selection, and accounting services handle the lifecycle. The
   controller closes with model-budget and simulated-compute gates; evidence
   and a storage seal are retained. Model outputs, scores, and compute are
   synthetic. This is an integration proof, not an optimization result or live
   evaluator qualification. Clean candidate-session resume is now exercised
   with real OpenCode and reconstructed durable services. Registered recovery,
   matched peer-arm wiring, and capability denial auditing remain explicit gates.
4. **Registered-study promotion: inventoried, not authorized.** Existing
   missing budget, analysis, block, provider/runtime, compute, stability/shortcut,
   enforcement, and platform-build authorities remain unresolved. Nothing in
   this implementation chooses a study spending envelope or authorizes live
   scored runs.

The candidate upload path now accepts an optional actor-scoped storage
idempotency key. The key and artifact reference commit together, so retries
reuse the original artifact instead of creating conflicting submission IDs.

Validation for the preceding increment: 283 tests ran in the default suite (269 passed,
14 skipped). Seven opt-in real-runtime integration tests passed across the
four-condition rehearsal, candidate flow, and existing restart/peer tests.
JavaScript syntax checks and `git diff --check` passed. All model and evaluator
outputs used in these runs were synthetic; no external model or GPU spend was
incurred. Commit `dde55d9` contains the preceding hardening work; this increment
and the follow-up below form the next implementation checkpoint.

### Candidate recovery and capability transport follow-up

- Completed: development solo resume reconstructs the durable candidate services,
  rotates the capability, restores the original OpenCode session, and reads its
  public result. The optional `--restart-runtime` rehearsal persists and reloads
  the checkpoint and verifies that acknowledged delivery replay causes no extra
  model call or compute reservation. Its schema v2 audit records these checks.
- Completed: resume rollback releases every provisional bridge and capability,
  including when receipt restoration or another cleanup step fails.
- Completed: candidate and native-admission services can use separate
  per-capability Unix listeners with no host HTTP listener. Local integration
  tests exercise both services and candidate submission retries across normal
  capability rotation. Revocation removes the listener and session binding.
- Pending: OCI relay wiring and Linux conformance. The harness rejects these
  new Unix capability transports until relay integration is implemented. Neither
  a pinned image nor a Linux container deployment is available on this host.
- Pending: matched peer-arm candidate wiring, registered capability denial
  auditing, live agent-to-evaluator composition, and registered recovery.
  The restart test covers clean checkpoints, not an entire deployment crash;
  the synthetic model gateway stays running. Existing missing-event recovery
  remains unscoreable.

No new live model or GPU runs were authorized. The unrelated
progress-assessment draft is excluded from this implementation checkpoint.

Validation: the default suite ran 289 tests (272 passed, 17 skipped), with
`ResourceWarning` treated as an error. Ten enabled local integration tests also
passed: four existing OpenCode recovery/peer tests, two adapter rehearsals,
two candidate-runtime rehearsals, and two Unix capability tests. JavaScript
syntax, CLI help, readiness inventory, and whitespace checks passed. All model
responses and evaluator outcomes were synthetic; these tests incurred no
external model or GPU spend.
