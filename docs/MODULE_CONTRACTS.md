# Module Contracts

## Purpose

These contracts keep the experimental domain independent of OpenCode and of any one task family. V0 implements only the adapters needed for the first study, but harness, collaboration, compute, research, storage and evaluation remain separate boundaries.

OpenCode implements `HarnessRuntime`. It is both the single-agent runtime and the source of the conventional native-multi-agent baseline; there is no second orchestration harness.

## Dependency direction

```text
cli
  -> experiment application
       -> campaign definition
       -> domain ports
            HarnessRuntime
            CollaborationBackend
            ComputeBackend
            ResearchBackend
            StorageBackend
            Evaluator
       -> enforcement services
            BudgetGateway
            ComputeBroker
            ResearchBroker
            SandboxPolicy
```

Concrete adapters depend inward on the port types. The campaign definition declares required capabilities and outcome semantics but cannot instantiate adapters. The composition root is the only module that binds ports to implementations.

## Study and run manifests

Every study version freezes the complete randomized block plan before its first confirmatory run:

```text
StudyManifest
  study_id: string
  study_version: string
  conditions: [solo, native_multiagent, peer_isolated, peer_collab]
  organisation_size: integer  # N; initial pilot uses 4
  harness: AdapterRef + HarnessProfile
  instrumentation: InstrumentationProfile
  collaboration: AdapterRef + CollaborationProfile
  compute: AdapterRef + ComputeProfile
  research: AdapterRef + ResearchProfile
  storage: AdapterRef + StorageProfile
  evaluator: AdapterRef + EvaluatorProfile
  model: ModelProfile
  budget: BudgetEnvelope
  actor_allocation: ActorAllocationPolicy
  campaign: CampaignRef
  peer_activation: PeerActivationPolicy
  measurement: MeasurementProtocol
  submission_policy: SubmissionPolicy
  observer_policy: read_only | none
  scale_acceptance: ScaleAcceptanceProfile?  # required for a large-fleet study
  block_plan: RandomizedBlockPlan
  analysis_plan: AnalysisPlanRef

RandomizedBlockPlan
  algorithm: name + version
  master_seed: integer
  blocks: ordered list<BlockAssignment>

BlockAssignment
  block_id: string
  replicate_id: string
  variant_id: string
  task_seed: integer
  task_material_digest: digest
  runs: ordered list<RunAssignment>

RunAssignment
  run_id: string
  execution_position: integer
  run_stochastic_seed: integer
  actor_stochastic_seeds: map<actor_ordinal, integer>
  assigned_condition: solo | native_multiagent | peer_isolated | peer_collab

ResolvedRunManifest
  study_manifest_digest: digest
  block_id: string
  replicate_id: string
  variant_id: string
  run_id: string
  execution_position: integer
  task_seed: integer
  task_material_digest: digest
  run_stochastic_seed: integer
  actor_stochastic_seeds: map<actor_ordinal, integer>
  condition: solo | native_multiagent | peer_isolated | peer_collab
  resolved_configuration_digest: digest
```

The randomization algorithm assigns condition labels to already defined execution positions and stochastic seeds. `task_seed` generates one immutable material bundle for the whole block; its digest must be identical in all four resolved runs. Run and actor stochastic seeds control model/runtime randomness only and cannot alter missions, inputs, public workloads or evaluator data. The runner materializes and hashes every `BlockAssignment` before any outcome is observed. A resolved manifest cannot choose its condition, variant or seeds, and retries remain traceable to the original assignment.

`AdapterRef` records implementation name, semantic version or commit, configuration digest and declared capability version.

`ModelProfile` records provider, exact requested model identifier, expected returned identity or fingerprint when available, endpoint class, inference parameters, retry policy, price-catalog version and `ProviderCachePolicy`. Cache policy is one of `disabled`, `actor_run_scoped` or `provider_managed_observed`. A confirmatory peer comparison requires effective actor isolation through a provider namespace, a frozen non-semantic per-actor isolation prefix, or disabled caching; `provider_managed_observed` without isolation is calibration-only. V0 defines DeepSeek-direct Flash for engineering/smoke runs and DeepSeek-direct Pro for confirmatory runs after a preregistered feasibility qualification verifies durable tool use and at least one valid campaign artifact without inspecting condition differences. One exact profile is frozen across every condition and block; changing profile creates a new study version. No domain port contains provider-specific types.

`InstrumentationProfile` freezes the OpenCode plugin commit, plugin configuration digest, event-schema version, SDK event adapter version, buffering/backpressure policy and reconciliation policy. It is observational and condition-blind: it may attach run identity and record events, but cannot change prompts, model parameters, tools, permissions, scheduling or budgets.

`organisation_size` is the sole source of truth for fleet size `N`. `PeerActivationPolicy` freezes the start barrier, job-delivery schedule, maximum concurrency, wake/resume behavior and deadline handling; its resolved session count must validate against `organisation_size`. V0 uses `eager_all`: exactly `N` peer sessions are created and receive every job. The first pilot sets `N = 4`; later studies may register other sizes. Within a study, `N` is identical across arms and blocks. The controller applies the same policy to `peer_isolated` and `peer_collab` without inspecting condition-specific content or runtime activity.

`MeasurementProtocol` freezes resource reset, image and dependency digests, warmup, repetition count and order, reference canaries, environmental recording, contamination tolerances and condition-blind retry or invalidation rules.

`BudgetEnvelope` contains organisation-level maxima. `ActorAllocationPolicy` adds fixed actor-level maxima for the two peer conditions:

```text
BudgetEnvelope
  api_usd_cap: decimal
  wall_time_seconds: integer
  cloud_gpu_seconds: integer
  research_request_cap: integer
  research_byte_cap: integer
  max_candidate_submissions: integer
  max_provider_retries: integer

ActorAllocationPolicy
  mode: fixed_nontransferable
  api_usd_cap_per_actor: decimal
  cloud_gpu_seconds_per_actor: integer
  research_request_cap_per_actor: integer
  research_byte_cap_per_actor: integer
  candidate_submissions_per_actor: integer
  provider_retries_per_actor: integer
  compute_scheduler: SerializedSchedulerPolicy
```

For both peer conditions, every top-level actor receives the same fixed allocation and cannot consume another actor's allowance. `solo` and `native_multiagent` use the organisation envelope directly. This deliberately makes V0 a narrow test of information sharing. The `peer_collab - native_multiagent` contrast is a bundled comparison of operating approaches, because topology and allocation mechanics differ. Pooled or transferable allocation is a later registered treatment.

When present, `ScaleAcceptanceProfile` supplies numeric, study-configurable thresholds for a fake-runtime test: simulated actor count (at least 100 before a large-fleet study), maximum startup time, controller memory, event-lag percentile, dropped-event count, page size, broker admission latency, export time and permitted export loss. The profile also forbids all-to-all polling.

The study manifest, complete block plan, resolved run manifest and capability manifests are hashed before campaign start. Any effective configuration mismatch invalidates the run.

## Durable campaign domain

```text
CampaignInstance
  campaign_run_id: string
  study_id: string
  study_version: string
  block_id: string
  replicate_id: string
  variant_id: string
  run_id: string
  condition: CoordinationCondition
  organisation: Organisation
  jobs: ordered list<Job>
  status: pending | active | closed | failed
  started_at: timestamp | null
  closed_at: timestamp | null
```

`Organisation` owns stable top-level `AgentIdentity` values, harness-session handles, private workspace handles, collaboration scope, budget account and session-bound service bindings. These survive job boundaries and are destroyed or archived only when the campaign closes.

```text
interface CampaignController:
  start(spec: CampaignStartSpec) -> CampaignHandle
  deliver(handle, job: Job) -> JobHandle
  await_job(handle, job_handle, deadline) -> JobResult
  snapshot(handle) -> CampaignSnapshot
  close(handle, reason: StopReason) -> CampaignResult
```

The controller may schedule jobs and manage lifecycle. It may not decide agent roles, handoffs, peer messages, artifact merging or task solutions.

The first serving campaign supplies one evolving job. Later definitions may supply a sequence without changing this contract.

## Campaign definition

```text
interface CampaignDefinition:
  describe() -> CampaignDescriptor
  jobs(variant_id, task_seed) -> MaterializedJobs
  required_capabilities() -> CapabilityRequirements
  default_outcome(variant_id, job_id) -> DefaultOutcomeSpec
  outcome_policy(job_id) -> OutcomePolicy
  submission_policy() -> SubmissionPolicy
  aggregation_policy() -> OutcomeAggregation
```

`MaterializedJobs` contains the ordered jobs and their canonical material digest. The definition owns public missions, task materials, job ordering, submission schema, default-outcome behavior and the meaning of outcomes. It references evaluator, compute and research requirements declaratively. It cannot call provider APIs, provision GPUs, store events or inspect the experimental condition.

`DefaultOutcomeSpec` is campaign-specific. It may identify a frozen system-owned reference artifact to register as a candidate, or a terminal failure-floor outcome when no eligible candidate exists. `OutcomePolicy` defines a normalized public ordering criterion, validity gates and deterministic tie-break without giving the generic registry task-specific preferences. An optimization campaign can select the greatest valid public improvement over its reference; an economics or other generative campaign can order normalized criterion scores and use its declared failure floor. The same frozen specification applies to every condition.

## Harness runtime port

```text
interface HarnessRuntime:
  capabilities() -> HarnessCapabilities
  start_organisation(spec: OrganisationSpec) -> HarnessOrganisation
  create_primary(org, actor: AgentIdentity) -> SessionHandle
  deliver(session, job: Job) -> void
  events(org) -> async EventStream
  snapshot(org) -> HarnessSnapshot
  resume(snapshot) -> HarnessOrganisation
  stop(org, reason: StopReason) -> HarnessSnapshot
```

`OrganisationSpec` includes the condition, persistent workspace handles, model endpoint routed through `BudgetGateway`, tool grants, native-handoff policy and the frozen peer activation policy. Its maximum live identities are derived from `organisation_size`: one for `solo`, and at most `N` for every other condition.

The OpenCode adapter must:

- use a pinned OpenCode server and SDK version;
- load exactly the plugin commit, configuration digest and event schema in `InstrumentationProfile`;
- preserve top-level sessions and workspaces across jobs;
- create, start, deliver to, wake and stop peer sessions according to the same frozen activation policy in both peer conditions;
- deny native task/subagent calls outside `native_multiagent`;
- expose only pinned stock general-purpose subagents in `native_multiagent`;
- add no experiment-owned planner, supervisor, merger, critique or retry loop;
- emit all available parent, child, message, tool, timing and usage events;
- export the effective configuration and complete available session tree;
- stop every live session when the campaign closes.

Harness events are observational. The instrumentation plugin is condition-blind and cannot alter prompts, parameters, tools, permissions, scheduling or budgets. The adapter does not enforce the authoritative dollar limit, filesystem boundary, network policy or external capability quota.

## Artifact service

```text
interface ArtifactService:
  snapshot(session_transport, workspace_paths, metadata) -> ArtifactRef
  materialize(session_transport, source: ArtifactRef | PublicationId, destination) -> MaterializationReceipt
  publish(session_transport, scope, body, reply_to?, artifact_refs?) -> Entry
```

This application service is the only agent-facing path into or out of `StorageBackend`. `snapshot` derives campaign and actor from the session, canonicalizes bounded workspace paths, rejects symlink escape and writes an immutable actor-owned artifact. `materialize` writes only into the caller's workspace after verifying either ownership of an `ArtifactRef` or audience access to a `PublicationId`.

For publication, the service proves ownership, stores a server-side record mapping an opaque `PublicationId` to campaign, owner, artifact and audience, and places only that identifier in the collaboration entry. Every materialization is reauthorized from the caller's authenticated session; the identifier is not a bearer capability. The service contains no recommendation, task-allocation or artifact-merging policy.

`ArtifactRef` denotes an admitted ordinary artifact. `QuarantinedArtifact` is a distinct type and is rejected by snapshot, materialize, publication, compute staging, submission and evaluation. Only a campaign-approved isolated renderer or extractor may consume a quarantined object; after applying its declared checks, it may create a new `SanitizedDocument` or ordinary `ArtifactRef` with lineage back to the quarantine digest.

## Collaboration backend port

```text
interface CollaborationBackend:
  provision(campaign_run_id, visibility: none | actor_private | organisation_shared) -> CollaborationScope
  publish(scope, actor_context, body, reply_to?, publication_ids?) -> Entry
  list_recent(scope, actor_context, cursor?, limit?) -> Page<Entry>
  get_thread(scope, actor_context, entry_id) -> list<Entry>
  search(scope, actor_context, query, cursor?, limit?) -> Page<Entry>
  notifications(scope, actor_context, cursor?, limit?) -> Page<Notification>
  export(scope) -> CollaborationSnapshot
  reset(scope) -> void
```

The server derives campaign and actor identity from authenticated tool context; agent-supplied identifiers are ignored.

Visibility rules:

- `solo` and `native_multiagent`: `none`; the tool is unavailable.
- `peer_isolated`: `actor_private`; the peer-tool schema is present but each actor can observe only its own entries and referenced artifacts.
- `peer_collab`: `organisation_shared`; every top-level peer in the organisation can observe shared entries and artifacts.

The two peer modes must use the same implementation version, persistence rules, pagination, tool descriptions and operation limits wherever truthful. Authorization checks—not client filtering—enforce visibility.

Collaboration visibility is the only cross-actor publication path in the peer comparison. Candidate and evaluator feedback, broker results, queue metadata, caches, files and artifacts remain actor-private until their owner explicitly publishes content or an authorized `PublicationId` in `peer_collab`.

`PublicationId` is opaque and resolves only inside `ArtifactService`. The identifier alone grants no authority: every read is checked against the caller's server-derived campaign and the publication audience. A deliberately published identifier therefore resolves for an authorized same-campaign peer, while guessing it or replaying it from an unauthorized session or campaign fails. An `actor_private` scope cannot create an organisation-audience publication. This preserves explicit sharing without exposing transferable signed grants to agents or coupling the storage and collaboration adapters.

## Session-bound service identity

Every agent-facing broker is reached through a session-bound, non-exportable transport created by the harness, such as a per-session Unix socket or local sidecar channel. The endpoint is mounted only in that session sandbox; it authenticates the peer process and derives campaign, actor and session identity server-side. Model text, tool arguments, headers and workspace files contain no bearer credential. Copied tool arguments, publication identifiers or request handles cannot change that identity or bypass an audience check; an authorized peer may resolve a publication deliberately addressed to its audience.

## Compute backend and broker

```text
interface ComputeBackend:
  capabilities() -> ComputeCapabilities
  provision(spec: ResourceSpec) -> ResourceHandle
  acquire_exclusive(handle, lease_spec: LeaseSpec) -> ResourceLease
  reset(lease, reset_spec: ResetSpec) -> ResetReceipt
  execute(lease, input_bundle: ImmutableInputBundle, command_spec) -> BackendExecutionResult
  run_canary(lease, canary_spec: CanarySpec) -> CanaryResult
  release_lease(lease) -> void
  usage(handle) -> ComputeUsage
  release(handle) -> void

interface ComputeBroker:
  submit(session_transport, request: ComputeJobRequest) -> ComputeJobHandle
  status(session_transport, job_handle) -> accepted | complete | failed
  result(session_transport, job_handle) -> ExecutionResult
```

`ComputeBackend` knows how to operate a cloud GPU, local fake or replay target. It does not know the experimental condition, agent prompts or campaign outcome.

`ComputeJobRequest` names actor-owned admitted `ArtifactRef` inputs, a declared command and expected output paths. `ComputeBroker` derives actor and campaign from the session transport, verifies ownership and digests, rejects quarantined inputs, stages the inputs as a read-only `ImmutableInputBundle`, then enforces command allowlists, exclusive leases, concurrency and the actor's fixed GPU-time quota. The backend returns declared output files to the broker; the broker stores them as new actor-owned artifacts and exposes their references in `ExecutionResult`. Infrastructure credentials remain behind the broker. An actor can observe only its own handles and terminal coarse status; it cannot inspect another actor's request, queue position, cache state or output. `running`, queue position and estimated-start fields are intentionally absent.

In both peer conditions, each actor has the same fixed, non-transferable GPU-time allocation. A work-conserving serialized scheduler uses one frozen condition-blind algorithm and randomized actor order, matched by actor ordinal across paired runs. It may use otherwise idle capacity, but never transfers quota. Scheduling metadata is ledger-only and unavailable during the agent-visible phase. Agents can still observe when their own work completes, so demand-dependent latency is an acknowledged low-bandwidth signal; it is recorded and analyzed as a sensitivity check.

Only one agent experiment or evaluator measurement may hold a GPU lease at a time. Hidden measurements begin only after the agent-visible phase closes and follow a separate condition-blind evaluation schedule. Scored execution follows the `MeasurementProtocol`: restore the declared image and clean state, record relevant device and software state, perform fixed warmup, run the frozen repetitions in the frozen order, and bracket candidate measurements with reference canaries. Canary drift beyond tolerance triggers the predeclared retry or invalidation rule, never an ad hoc condition-specific decision. Each reset, lease, repetition, canary and release emits a receipt.

## Research backend and broker

```text
interface ResearchBackend:
  capabilities() -> ResearchCapabilities
  search(scope, query) -> SearchResult
  fetch(scope, resource_id) -> SanitizedDocument | QuarantinedArtifact
  export(scope) -> ResearchSnapshot
  reset(scope) -> void

interface ResearchBroker:
  search(session_transport, query) -> SearchResult
  fetch(session_transport, resource_id) -> SanitizedDocument | QuarantinedArtifact
```

Adapters may implement disabled research, a frozen corpus or controlled live access. The broker enforces allowed sources, fixed per-actor request and byte quotas, recording and scope isolation. Search history, fetch results and caches are actor-private in both peer conditions; one actor's use cannot reduce another's allowance. An owner may explicitly publish a result through the collaboration backend only in `peer_collab`. Research state cannot cross campaign runs.

Controlled live access must satisfy all of the following:

- permit only approved HTTP(S) destinations and methods; the harness sandbox has no direct network fallback;
- resolve and validate every connection and redirect hop, pin the validated address for the connection, and defend against DNS rebinding;
- block loopback, private, link-local, multicast, reserved and cloud-provider metadata addresses for IPv4 and IPv6;
- cap redirect count, request count, transfer bytes, decompressed bytes and archive expansion;
- enforce a declared MIME allowlist using both response headers and content sniffing;
- strip ambient credentials and prevent agent-controlled cookies or authorization headers unless explicitly granted;
- decode allowed textual responses into a non-executable `SanitizedDocument` after removing active content and applying encoding and size limits;
- store binary, archive or active content as a content-addressed `QuarantinedArtifact`, unreadable to agent tools and rejected by ordinary artifact consumers until a campaign policy names an isolated safe renderer or extractor and its output is admitted as a new sanitized document or ordinary artifact;
- record URL, resolved address, redirect chain, headers needed for audit, byte counts, content digest and every denial.

## Storage backend port

```text
interface StorageBackend:
  open_campaign(campaign_run_id, artifact_policy: ArtifactVisibilityPolicy) -> StorageScope
  append(scope, event: RunEvent) -> SequenceNumber
  put_artifact(scope, actor_context, bytes, media_type, metadata) -> ArtifactRef
  get_artifact(scope, actor_context, ref) -> bytes
  save_snapshot(scope, CampaignSnapshot) -> SnapshotRef
  export(scope) -> CampaignExport
  reset(scope) -> void
  seal(scope, final_manifest, checksums) -> StorageReceipt
```

The V0 local adapter may use JSONL, SQLite and content-addressed files. It guarantees monotonic event sequence numbers, immutable artifact hashes, atomic snapshots and campaign-scoped export.

Storage records evidence but does not infer validity or enforce budgets. Its authorization layer enforces campaign and owner visibility; guessing a digest does not reveal existence or metadata. Agent-facing reads and writes use `ArtifactService`. Cross-actor reads occur only after that service authorizes the caller against its server-side publication record. Storage never promotes visibility automatically.

## Evaluator port

```text
interface Evaluator:
  capabilities() -> EvaluatorCapabilities
  validate(candidate: ArtifactRef, variant: VariantRef) -> ValidationResult
  visible_evaluate(candidate: ArtifactRef, variant: VariantRef) -> VisibleResult
  hidden_evaluate(candidate: ArtifactRef, variant: VariantRef) -> HiddenResult
  default_outcome(spec: DefaultOutcomeSpec, variant: VariantRef) -> EvaluationOutcome
```

The evaluator receives immutable artifacts, frozen variant data and an evaluation resource lease. It does not receive the experimental condition, agent identities or collaboration trace.

Visible evaluation may be invoked only through the submission service and within its cap. Its result is visible only to the submitting actor unless that actor explicitly publishes it in `peer_collab`. Hidden evaluation is authorized only after submission closure and final selection. Evaluator measurements on shared hardware use exclusive compute leases and the same frozen reset, warmup, repetition and canary protocol.

## Submission registry

```text
interface SubmissionRegistry:
  initialize(campaign_run_id, job_id, default_outcome: DefaultOutcomeSpec, policy: OutcomePolicy) -> void
  submit(session_transport, job_id, artifact, metadata) -> CandidateReceipt
  visible_result(session_transport, receipt) -> VisibleResult
  close(campaign_run_id, job_id?) -> SubmissionSet
  select(submissions: SubmissionSet, policy: OutcomePolicy) -> SelectionResult
```

The agent-facing submission methods derive campaign and actor from the session transport and verify that `job_id` belongs to that campaign. Submissions are immutable. Candidate existence, artifact metadata and public feedback are actor-private while work remains open; `peer_isolated` actors cannot enumerate or infer one another's submissions. Each peer has the same fixed, non-transferable submission allowance, so another actor cannot consume its capacity or cause a quota rejection. The registry may see all submissions for neutral post-closure selection but never turns aggregation into an agent-visible channel.

Limits and the neutral selector are identical across conditions. Each peer submission must stand alone; the registry never merges artifacts. `SelectionResult` contains either the best eligible candidate under the campaign's normalized public ordering and deterministic tie-break, or its declared default/failure outcome. A campaign-provided reference candidate participates like any other system-owned candidate. The same fallback and selector over `peer_isolated` and `peer_collab` are required for the communication estimand.

## Budget gateway

```text
interface BudgetAccount:
  provision_actor(actor_context, allocation: ActorBudgetAllocation) -> ActorBudgetHandle
  reserve(session_transport, request_context: ModelCallContext, maximum_usd) -> Reservation | Rejected
  settle(reservation_id, ProviderUsage) -> Charge
  release(reservation_id, reason) -> void
  snapshot() -> BudgetSnapshot
```

Enforcement rules:

- Every model credential available to a campaign terminates at the gateway.
- Sandbox network policy denies direct model-provider egress.
- Primary, peer, subagent, retry, compaction and auxiliary model calls remain under one organisation account.
- In both peer conditions, each top-level actor is additionally constrained by the same fixed, non-transferable suballocation. A request is admitted only if both its actor allocation and organisation envelope can cover the reservation.
- Reservations use decimal arithmetic, conservative maximums and a versioned price catalog.
- Requests that could exceed the cap are rejected before provider execution.
- Provider usage and receipts are retained unchanged and reconciled after settlement.
- Every response records requested and returned model identifiers, available revision and system fingerprint, provider request ID, cached input/output accounting and the effective cache policy.
- Gateway-controlled caches use separate `(campaign, actor)` namespaces in both peer conditions and are never shared across actors or campaign runs or selectively prewarmed by condition. Provider-managed isolation keys or a frozen non-semantic per-actor isolation prefix are used where needed; a provider that cannot provide effective isolation is calibration-only.
- Subscription-backed model access is forbidden in confirmatory studies.

OpenCode's cost and token fields are retained as observational telemetry. They are reconciled against the gateway but never authorize requests, extend the cap or replace provider-accounting evidence.

The gateway derives campaign, actor and harness session from the same non-exportable session transport used by the brokers. `ModelCallContext` identifies only the call itself. Model-supplied or agent-supplied identity headers cannot select another actor's account or cache namespace.

An actor may observe only its own charges, remaining allocation and rejections. Because the peer allocations partition the organisation envelope and unused capacity is not reassigned, another peer's activity cannot cause an actor's model call to be admitted or rejected in V0.

The study declares how identity drift is handled before execution. A persistent returned model or fingerprint change creates a new `study_version`; a change within a randomized block normally invalidates and reruns the whole block. Missing provider identity fields are recorded explicitly rather than inferred.

## Sandbox policy

The harness runtime launches inside an enforced sandbox profile that declares writable paths, process limits, network destinations and per-session broker endpoints. It mounts the session-bound local transport but no bearer, provider, cloud or storage credential. Raw network egress is denied; live research is available only through the broker's SSRF-resistant fetch path, and quarantined artifacts are neither readable nor executable until an explicit safe-extraction policy admits derived output.

The sandbox must prevent bypassing the budget gateway or capability brokers. Logging an attempted bypass without blocking it is a control failure.

## Event model

Minimum event kinds include:

- campaign and job lifecycle;
- harness session lifecycle and native handoffs;
- model reservations, settlements and rejections;
- requested/returned model identity, fingerprint, cache usage and provider receipts;
- tool calls and results;
- collaboration reads, writes and authorization denials;
- artifact-publication creation, resolution and denial;
- compute leases, resets, repetitions, canaries and releases;
- compute and research broker admissions, actor-visible status, results and denials;
- candidate submissions, public evaluation, selection and hidden evaluation;
- snapshots, infrastructure errors and validity decisions;
- human post-run observations.

Events and OpenCode traces are evidence. The corresponding gateway, sandbox, authorization or broker remains the enforcement authority.

## Conformance requirements

Before the first confirmatory pilot, tests must prove:

- A fake `HarnessRuntime` can run the core lifecycle without importing OpenCode.
- The registered block plan deterministically produces the same resolved run manifests, and no run may alter its assigned condition, variant, task seed or stochastic seeds.
- All four arms in a block receive byte-identical canonical task-material digests; changing a run or actor stochastic seed cannot change that digest.
- Every condition and block uses the exact registered model profile; a capability-check failure cannot trigger an in-study model substitution.
- The pinned instrumentation plugin/configuration and event schema are identical across arms, observational and condition-blind; sequence, loss detection, bounded buffering/backpressure and gateway/tool reconciliation pass under forced load.
- OpenCode sessions and workspaces survive delivery of a second job in the same campaign.
- `organisation_size` is the only fleet-size input; each condition derives and validates its allowed live-session count from it.
- Native handoffs work only in `native_multiagent` and respect `N`.
- Both peer modes eagerly activate exactly `N` sessions under the same start barrier, delivery schedule, concurrency and deadline rules.
- `peer_isolated` and `peer_collab` expose the same peer-tool schema.
- Cross-actor collaboration-entry reads fail in `peer_isolated`; explicitly published entries and authorized publication identifiers succeed within the organisation in `peer_collab`.
- Raw artifact references and guessed, unpublished, wrong-audience or cross-campaign publication identifiers fail closed; a deliberately published identifier resolves for its authorized audience.
- Cross-actor candidate discovery, public feedback, broker jobs/results, queue metadata, caches and artifact reads fail in `peer_isolated`; in `peer_collab`, they remain private until explicitly published through the service.
- Cross-campaign collaboration, artifact, research-cache and event reads fail.
- All model sessions remain under one organisation envelope and cannot reach the provider directly; peer actors also cannot exceed, transfer or observe one another's fixed suballocations.
- Copying or replaying tool arguments, request handles, publication identifiers or model headers from another session never changes the broker-derived campaign or actor or bypasses publication-audience checks; the sandbox exposes no transferable service credential.
- Authenticated artifact snapshotting rejects paths outside the caller's workspace and creates immutable artifacts owned by the server-derived actor; materialization cannot escape the caller's workspace.
- Submission ownership and feedback visibility derive from the authenticated session, not caller-supplied actor or campaign fields.
- Compute and research calls cannot bypass their brokers or quotas.
- Compute staging rejects unowned or mutable inputs, mounts the accepted bundle read-only and records declared outputs as new artifacts owned by the submitting actor.
- `QuarantinedArtifact` is rejected by artifact publication/materialization, compute, submission and evaluation; only the approved isolated extraction path can produce a separately admitted artifact with recorded lineage.
- The work-conserving serialized scheduler uses the frozen condition-blind policy and matched randomized actor order in both peer arms; callers can see only their own terminal coarse status, not position, competing demand or scheduling metadata.
- GPU measurements hold exclusive leases and deterministically apply reset, warmup, repetition and canary rules.
- Controlled web access rejects redirect and DNS-rebinding attempts to private or metadata addresses, sanitizes allowed text, and keeps binary or active content quarantined unless an approved isolated extractor produces separately admitted output.
- Every settled provider response is reconciled with observational harness telemetry.
- Returned provider identity, fingerprint and cache accounting are captured and identity drift applies the frozen block-validity rule.
- Hidden evaluator inputs and outputs never enter agent-visible storage.
- The organisation-level candidate total, campaign-specific default outcomes and neutral public-score selection are identical across all four conditions; the two peer arms also use identical fixed actor sublimits.
- A campaign definition can swap compute, research, storage and evaluator adapters without source changes.

Before a large-fleet study, the manifest's `ScaleAcceptanceProfile` must be satisfied using a fake runtime. It supplies numeric thresholds rather than architecture constants: simulated actors (at least 100 for the first scale gate), startup duration, controller peak memory, event-lag percentile, dropped-event count, maximum page size, broker-admission latency, campaign-export duration and export-loss allowance. The test must also demonstrate independent cursors, scheduler fairness, bounded backpressure and no all-to-all polling.

## Change policy

Adapter versions, instrumentation profiles, capability manifests, model profiles, common instructions, tool schemas, price catalogs, sandbox policies, budgets, actor allocations, scheduler policies, block assignments, scale thresholds, analysis plans, campaign definitions, evaluators and selection rules may change during calibration. After registration, any such change requires a new `study_version` and a new complete set of condition runs.
