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
  submission_policy: SubmissionPolicy
  observer_policy: read_only | none
```

`AdapterRef` records implementation name, semantic version or commit, configuration digest and declared capability version.

`ModelProfile` records provider, model identifier, endpoint class, inference parameters, retry policy and price-catalog version. DeepSeek may be the first profile, but no port contains DeepSeek-specific types.

`BudgetEnvelope` contains at least:

```text
api_usd_cap: decimal
wall_time_seconds: integer
cloud_gpu_seconds: integer
research_request_cap: integer
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
  submission_policy() -> SubmissionPolicy
  aggregation_policy() -> OutcomeAggregation
```

The definition owns public missions, task materials, job ordering, submission schema and the meaning of outcomes. It references evaluator, compute and research requirements declaratively. It cannot call provider APIs, provision GPUs, store events or inspect the experimental condition.

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

`OrganisationSpec` includes the condition, maximum live identities, persistent workspace handles, model endpoint routed through `BudgetGateway`, tool grants and native-handoff policy.

The OpenCode adapter must:

- use a pinned OpenCode server and SDK version;
- preserve top-level sessions and workspaces across jobs;
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

## Compute backend and broker

```text
interface ComputeBackend:
  capabilities() -> ComputeCapabilities
  provision(spec: ResourceSpec) -> ResourceHandle
  execute(handle, content_digest, command_spec) -> ExecutionResult
  usage(handle) -> ComputeUsage
  release(handle) -> void

interface ComputeBroker:
  execute(actor_context, capability_token, request) -> ExecutionResult
```

`ComputeBackend` knows how to operate a cloud GPU, local fake or replay target. It does not know the experimental condition, agent prompts or campaign outcome.

`ComputeBroker` is the enforcement boundary. It validates actor/run identity, capability scope, command allowlists, content digests, leases, concurrent execution and GPU-time quota. Infrastructure credentials remain behind the broker.

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

Adapters may implement disabled research, a frozen corpus or controlled live access. The broker enforces allowed sources, request and byte quotas, recording and scope isolation. Cache and query visibility follow the manifest and cannot cross campaign runs.

## Storage backend port

```text
interface StorageBackend:
  open_campaign(campaign_run_id) -> StorageScope
  append(scope, event: RunEvent) -> SequenceNumber
  put_artifact(scope, bytes, media_type, metadata) -> ArtifactRef
  get_artifact(scope, actor_context, ref) -> bytes
  save_snapshot(scope, CampaignSnapshot) -> SnapshotRef
  export(scope) -> CampaignExport
  seal(scope, final_manifest, checksums) -> StorageReceipt
```

The V0 local adapter may use JSONL, SQLite and content-addressed files. It guarantees monotonic event sequence numbers, immutable artifact hashes, atomic snapshots and campaign-scoped export.

Storage records evidence but does not infer validity or enforce budgets. Its authorization layer does enforce campaign, actor-private and organisation-shared artifact visibility.

## Evaluator port

```text
interface Evaluator:
  capabilities() -> EvaluatorCapabilities
  validate(candidate: ArtifactRef, variant: VariantRef) -> ValidationResult
  visible_evaluate(candidate: ArtifactRef, variant: VariantRef) -> VisibleResult
  hidden_evaluate(candidate: ArtifactRef, variant: VariantRef) -> HiddenResult
```

The evaluator receives immutable artifacts, frozen variant data and an evaluation resource lease. It does not receive the experimental condition, agent identities or collaboration trace.

Visible evaluation may be invoked only through the submission service and within its cap. Hidden evaluation is authorized only after submission closure and final selection.

## Submission registry

```text
interface SubmissionRegistry:
  submit(campaign_run_id, job_id, actor_context, artifact, metadata) -> CandidateReceipt
  close(campaign_run_id, job_id?) -> list<CandidateReceipt>
  select(receipts, public_results, policy) -> CandidateReceipt
```

Submissions are immutable. Limits and the neutral selector are identical across conditions. Each peer submission must stand alone; the registry never merges artifacts. The same selector over `peer_isolated` and `peer_collab` is required for the communication estimand.

## Budget gateway

```text
interface BudgetAccount:
  reserve(request_id, maximum_usd) -> Reservation | Rejected
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
- Subscription-backed model access is forbidden in confirmatory studies.

OpenCode's cost and token fields are retained as observational telemetry. They are reconciled against the gateway but never authorize requests, extend the cap or replace provider-accounting evidence.

## Sandbox policy

The harness runtime launches inside an enforced sandbox profile that declares writable paths, process limits, network destinations and capability-broker endpoints. Agents receive broker-scoped tokens, never provider, cloud or storage credentials.

The sandbox must prevent bypassing the budget gateway or capability brokers. Logging an attempted bypass without blocking it is a control failure.

## Event model

Minimum event kinds include:

- campaign and job lifecycle;
- harness session lifecycle and native handoffs;
- model reservations, settlements and rejections;
- tool calls and results;
- collaboration reads, writes and authorization denials;
- compute and research broker admissions, results and denials;
- candidate submissions, public evaluation, selection and hidden evaluation;
- snapshots, infrastructure errors and validity decisions;
- human post-run observations.

Events and OpenCode traces are evidence. The corresponding gateway, sandbox, authorization or broker remains the enforcement authority.

## Conformance requirements

Before the first confirmatory pilot, tests must prove:

- A fake `HarnessRuntime` can run the core lifecycle without importing OpenCode.
- OpenCode sessions and workspaces survive delivery of a second job in the same campaign.
- Native handoffs work only in `native_multiagent` and respect `N`.
- `peer_isolated` and `peer_collab` expose the same peer-tool schema.
- Cross-actor reads fail in `peer_isolated` and succeed within the organisation in `peer_collab`.
- Cross-campaign collaboration, artifact, research-cache and event reads fail.
- All model sessions share one enforced budget account and cannot reach the provider directly.
- Compute and research calls cannot bypass their brokers or quotas.
- Every settled provider response is reconciled with observational harness telemetry.
- Hidden evaluator inputs and outputs never enter agent-visible storage.
- Candidate limits and neutral selection are identical across all four conditions.
- A campaign definition can swap compute, research, storage and evaluator adapters without source changes.

## Change policy

Adapter versions, capability manifests, model profiles, common instructions, tool schemas, price catalogs, sandbox policies, budgets, campaign definitions, evaluators and selection rules may change during calibration. After registration, any such change requires a new `study_version` and a new complete set of condition runs.
