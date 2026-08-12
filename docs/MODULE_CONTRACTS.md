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

## Study manifest

Every study version freezes:

```text
StudyManifest
  study_id: string
  study_version: string
  replicate_id: string
  random_seed: integer
  condition: solo | native_multiagent | peer_isolated | peer_collab
  organisation_size: integer
  harness: AdapterRef + HarnessProfile
  collaboration: AdapterRef + CollaborationProfile
  compute: AdapterRef + ComputeProfile
  research: AdapterRef + ResearchProfile
  storage: AdapterRef + StorageProfile
  evaluator: AdapterRef + EvaluatorProfile
  model: ModelProfile
  budget: BudgetEnvelope
  campaign: CampaignRef
  peer_activation: PeerActivationPolicy
  measurement: MeasurementProtocol
  submission_policy: SubmissionPolicy
  observer_policy: read_only | none
```

`AdapterRef` records implementation name, semantic version or commit, configuration digest and declared capability version.

`ModelProfile` records provider, requested model identifier, expected returned identity or fingerprint when available, endpoint class, inference parameters, retry policy, price-catalog version and `ProviderCachePolicy`. Cache policy is one of `disabled`, `actor_run_scoped` or `provider_managed_observed`. A confirmatory peer comparison requires effective actor isolation through a provider namespace, a frozen non-semantic per-actor isolation prefix, or disabled caching; `provider_managed_observed` without isolation is calibration-only. V0 defaults to the DeepSeek direct API and qualifies Flash and Pro through this same profile; no port contains DeepSeek-specific types.

`PeerActivationPolicy` freezes the number of top-level sessions, start barrier, job-delivery schedule, maximum concurrency, wake/resume behavior and deadline handling. V0 uses `eager_all`: exactly `N` peer sessions are created and receive every job. The controller applies the same policy to `peer_isolated` and `peer_collab` without inspecting condition-specific content or runtime activity.

`MeasurementProtocol` freezes resource reset, image and dependency digests, warmup, repetition count and order, reference canaries, environmental recording, contamination tolerances and condition-blind retry or invalidation rules.

`BudgetEnvelope` contains at least:

```text
api_usd_cap: decimal
wall_time_seconds: integer
cloud_gpu_seconds: integer
research_request_cap: integer
research_byte_cap: integer
max_live_agents: integer
max_candidate_submissions: integer
max_provider_retries: integer
```

The resolved manifest and capability manifests are hashed before campaign start. Any effective configuration mismatch invalidates the run.

## Durable campaign domain

```text
CampaignInstance
  campaign_run_id: string
  study_id: string
  condition: CoordinationCondition
  organisation: Organisation
  jobs: ordered list<Job>
  status: pending | active | closed | failed
  started_at: timestamp | null
  closed_at: timestamp | null
```

`Organisation` owns stable top-level `AgentIdentity` values, harness-session handles, private workspace handles, collaboration scope, budget account and capability grants. These survive job boundaries and are destroyed or archived only when the campaign closes.

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
  jobs(variant_id, seed) -> ordered list<Job>
  required_capabilities() -> CapabilityRequirements
  default_outcome(variant_id, job_id) -> DefaultOutcomeSpec
  outcome_policy(job_id) -> OutcomePolicy
  submission_policy() -> SubmissionPolicy
  aggregation_policy() -> OutcomeAggregation
```

The definition owns public missions, task materials, job ordering, submission schema, default-outcome behavior and the meaning of outcomes. It references evaluator, compute and research requirements declaratively. It cannot call provider APIs, provision GPUs, store events or inspect the experimental condition.

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

`OrganisationSpec` includes the condition, maximum live identities, persistent workspace handles, model endpoint routed through `BudgetGateway`, tool grants, native-handoff policy and the frozen peer activation policy.

The OpenCode adapter must:

- use a pinned OpenCode server and SDK version;
- preserve top-level sessions and workspaces across jobs;
- create, start, deliver to, wake and stop peer sessions according to the same frozen activation policy in both peer conditions;
- deny native task/subagent calls outside `native_multiagent`;
- expose only pinned stock general-purpose subagents in `native_multiagent`;
- add no experiment-owned planner, supervisor, merger, critique or retry loop;
- emit all available parent, child, message, tool, timing and usage events;
- export the effective configuration and complete available session tree;
- stop every live session when the campaign closes.

Harness events are observational. The adapter does not enforce the authoritative dollar limit, filesystem boundary, network policy or external capability quota.

## Collaboration backend port

```text
interface CollaborationBackend:
  provision(campaign_run_id, visibility: none | actor_private | organisation_shared) -> CollaborationScope
  publish(scope, actor_context, body, reply_to?, artifact_refs?) -> Entry
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

Collaboration visibility is the only cross-actor publication path in the peer comparison. Candidate and evaluator feedback, broker results, queue metadata, caches, files and artifacts remain actor-private until their owner explicitly publishes content or a permitted artifact reference in `peer_collab`. Publishing cannot grant access to an artifact that the authenticated actor is not authorized to read.

## Compute backend and broker

```text
interface ComputeBackend:
  capabilities() -> ComputeCapabilities
  provision(spec: ResourceSpec) -> ResourceHandle
  acquire_exclusive(handle, lease_spec: LeaseSpec) -> ResourceLease
  reset(lease, reset_spec: ResetSpec) -> ResetReceipt
  execute(lease, content_digest, command_spec) -> ExecutionResult
  run_canary(lease, canary_spec: CanarySpec) -> CanaryResult
  release_lease(lease) -> void
  usage(handle) -> ComputeUsage
  release(handle) -> void

interface ComputeBroker:
  submit(actor_context, capability_token, request) -> ComputeJobHandle
  status(actor_context, job_handle) -> ActorVisibleJobStatus
  result(actor_context, job_handle) -> ExecutionResult
```

`ComputeBackend` knows how to operate a cloud GPU, local fake or replay target. It does not know the experimental condition, agent prompts or campaign outcome.

`ComputeBroker` is the enforcement boundary. It validates actor/run identity, capability scope, command allowlists, content digests, exclusive leases, concurrent execution and GPU-time quota. Infrastructure credentials remain behind the broker. An actor can observe only its own handles, coarse status and results; it cannot inspect another actor's request, queue position, cache state, timing or output. The broker uses the same scheduling discipline in both peer conditions.

Only one agent experiment or evaluator measurement may hold a GPU lease at a time. Scored execution follows the manifest's `MeasurementProtocol`: restore the declared image and clean state, record relevant device and software state, perform fixed warmup, run the frozen repetitions in the frozen order, and bracket candidate measurements with reference canaries. Canary drift beyond tolerance triggers the predeclared retry or invalidation rule, never an ad hoc condition-specific decision. Each reset, lease, repetition, canary and release emits a receipt.

## Research backend and broker

```text
interface ResearchBackend:
  capabilities() -> ResearchCapabilities
  search(scope, query) -> SearchResult
  fetch(scope, resource_id) -> Document
  export(scope) -> ResearchSnapshot
  reset(scope) -> void

interface ResearchBroker:
  search(actor_context, capability_token, query) -> SearchResult
  fetch(actor_context, capability_token, resource_id) -> Document
```

Adapters may implement disabled research, a frozen corpus or controlled live access. The broker enforces allowed sources, request and byte quotas, recording and scope isolation. Search history, fetch results and caches are actor-private in both peer conditions; an owner may explicitly publish a result through the collaboration backend only in `peer_collab`. Research state cannot cross campaign runs.

Controlled live access must satisfy all of the following:

- permit only approved HTTP(S) destinations and methods; the harness sandbox has no direct network fallback;
- resolve and validate every connection and redirect hop, pin the validated address for the connection, and defend against DNS rebinding;
- block loopback, private, link-local, multicast, reserved and cloud-provider metadata addresses for IPv4 and IPv6;
- cap redirect count, request count, transfer bytes, decompressed bytes and archive expansion;
- enforce a declared MIME allowlist using both response headers and content sniffing;
- strip ambient credentials and prevent agent-controlled cookies or authorization headers unless explicitly granted;
- store downloads content-addressed in a non-executable quarantine, scan or reject active/unsupported content, and expose only broker-issued document or artifact references;
- record URL, resolved address, redirect chain, headers needed for audit, byte counts, content digest and every denial.

## Storage backend port

```text
interface StorageBackend:
  open_campaign(campaign_run_id) -> StorageScope
  append(scope, event: RunEvent) -> SequenceNumber
  put_artifact(scope, actor_context, bytes, media_type, metadata) -> ArtifactRef
  get_artifact(scope, actor_context, ref) -> bytes
  save_snapshot(scope, CampaignSnapshot) -> SnapshotRef
  export(scope) -> CampaignExport
  seal(scope, final_manifest, checksums) -> StorageReceipt
```

The V0 local adapter may use JSONL, SQLite and content-addressed files. It guarantees monotonic event sequence numbers, immutable artifact hashes, atomic snapshots and campaign-scoped export.

Storage records evidence but does not infer validity or enforce budgets. Its authorization layer does enforce campaign, actor-private and organisation-shared artifact visibility. In `peer_isolated`, an actor cannot discover an artifact's existence or metadata merely by guessing its digest. In `peer_collab`, cross-actor artifact reads require an explicit authorized collaboration reference; storage never promotes visibility automatically.

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
  submit(campaign_run_id, job_id, actor_context, artifact, metadata) -> CandidateReceipt
  visible_result(campaign_run_id, actor_context, receipt) -> VisibleResult
  close(campaign_run_id, job_id?) -> SubmissionSet
  select(submissions: SubmissionSet, policy: OutcomePolicy) -> SelectionResult
```

Submissions are immutable. Candidate existence, artifact metadata and public feedback are actor-private while work remains open; `peer_isolated` actors cannot enumerate or infer one another's submissions. The registry may see all submissions for neutral post-closure selection but never turns aggregation into an agent-visible channel.

Limits and the neutral selector are identical across conditions. Each peer submission must stand alone; the registry never merges artifacts. `SelectionResult` contains either the best eligible candidate under the campaign's normalized public ordering and deterministic tie-break, or its declared default/failure outcome. A campaign-provided reference candidate participates like any other system-owned candidate. The same fallback and selector over `peer_isolated` and `peer_collab` are required for the communication estimand.

## Budget gateway

```text
interface BudgetAccount:
  reserve(context: ModelRequestContext, maximum_usd) -> Reservation | Rejected
  settle(reservation_id, ProviderUsage) -> Charge
  release(reservation_id, reason) -> void
  snapshot() -> BudgetSnapshot
```

Enforcement rules:

- Every model credential available to a campaign terminates at the gateway.
- Sandbox network policy denies direct model-provider egress.
- Primary, peer, subagent, retry, compaction and auxiliary model calls share one account.
- Reservations use decimal arithmetic, conservative maximums and a versioned price catalog.
- Requests that could exceed the cap are rejected before provider execution.
- Provider usage and receipts are retained unchanged and reconciled after settlement.
- Every response records requested and returned model identifiers, available revision and system fingerprint, provider request ID, cached input/output accounting and the effective cache policy.
- Gateway-controlled caches use separate `(campaign, actor)` namespaces in both peer conditions and are never shared across actors or campaign runs or selectively prewarmed by condition. Provider-managed isolation keys or a frozen non-semantic per-actor isolation prefix are used where needed; a provider that cannot provide effective isolation is calibration-only.
- Subscription-backed model access is forbidden in confirmatory studies.

OpenCode's cost and token fields are retained as observational telemetry. They are reconciled against the gateway but never authorize requests, extend the cap or replace provider-accounting evidence.

`ModelRequestContext` identifies campaign, actor, harness session and call. The gateway derives it from authenticated transport context; model-supplied or agent-supplied identity headers cannot select another actor's account or cache namespace.

The study declares how identity drift is handled before execution. A persistent returned model or fingerprint change creates a new `study_version`; a change within a randomized block normally invalidates and reruns the whole block. Missing provider identity fields are recorded explicitly rather than inferred.

## Sandbox policy

The harness runtime launches inside an enforced sandbox profile that declares writable paths, process limits, network destinations and capability-broker endpoints. Agents receive broker-scoped tokens, never provider, cloud or storage credentials. Raw network egress is denied; live research is available only through the broker's SSRF-resistant fetch path and quarantined artifacts are not executable or importable until admitted by explicit policy.

The sandbox must prevent bypassing the budget gateway or capability brokers. Logging an attempted bypass without blocking it is a control failure.

## Event model

Minimum event kinds include:

- campaign and job lifecycle;
- harness session lifecycle and native handoffs;
- model reservations, settlements and rejections;
- requested/returned model identity, fingerprint, cache usage and provider receipts;
- tool calls and results;
- collaboration reads, writes and authorization denials;
- compute leases, resets, repetitions, canaries and releases;
- compute and research broker admissions, actor-visible status, results and denials;
- candidate submissions, public evaluation, selection and hidden evaluation;
- snapshots, infrastructure errors and validity decisions;
- human post-run observations.

Events and OpenCode traces are evidence. The corresponding gateway, sandbox, authorization or broker remains the enforcement authority.

## Conformance requirements

Before the first confirmatory pilot, tests must prove:

- A fake `HarnessRuntime` can run the core lifecycle without importing OpenCode.
- OpenCode sessions and workspaces survive delivery of a second job in the same campaign.
- Native handoffs work only in `native_multiagent` and respect `N`.
- Both peer modes eagerly activate exactly `N` sessions under the same start barrier, delivery schedule, concurrency and deadline rules.
- `peer_isolated` and `peer_collab` expose the same peer-tool schema.
- Cross-actor collaboration-entry reads fail in `peer_isolated`; explicitly published entries and references succeed within the organisation in `peer_collab`.
- Cross-actor candidate discovery, public feedback, broker jobs/results, queue metadata, caches and artifact reads fail in `peer_isolated`; in `peer_collab`, they remain private until explicitly published.
- Cross-campaign collaboration, artifact, research-cache and event reads fail.
- All model sessions share one enforced budget account and cannot reach the provider directly.
- Compute and research calls cannot bypass their brokers or quotas.
- GPU measurements hold exclusive leases and deterministically apply reset, warmup, repetition and canary rules.
- Controlled web access rejects redirect and DNS-rebinding attempts to private or metadata addresses and enforces MIME, byte and quarantine policy.
- Every settled provider response is reconciled with observational harness telemetry.
- Returned provider identity, fingerprint and cache accounting are captured and identity drift applies the frozen block-validity rule.
- Hidden evaluator inputs and outputs never enter agent-visible storage.
- Candidate limits, campaign-specific default outcomes and neutral public-score selection are identical across all four conditions.
- A campaign definition can swap compute, research, storage and evaluator adapters without source changes.

Before a fleet-size study above V0, a fake-runtime load test with at least 100 simulated actors must demonstrate bounded pagination, independent notification cursors, event-stream backpressure, fair compute-queue admission and complete campaign export without all-to-all polling.

## Change policy

Adapter versions, capability manifests, model profiles, common instructions, tool schemas, price catalogs, sandbox policies, budgets, campaign definitions, evaluators and selection rules may change during calibration. After registration, any such change requires a new `study_version` and a new complete set of condition runs.
