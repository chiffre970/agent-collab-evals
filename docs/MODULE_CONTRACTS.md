# Module Contracts

## Purpose

These contracts keep the experimental domain independent of OpenCode and of any one task family. V0 implements only the adapters needed for the first study, but harness, collaboration, publication authorization, compute, research, storage and evaluation remain separate boundaries, and application/enforcement services are independently pinned at the composition root.

OpenCode implements `HarnessRuntime`. It is both the single-agent runtime and the source of the conventional native-multi-agent baseline; there is no second orchestration harness.

## Dependency direction

```text
cli
  -> experiment application
       -> campaign definition
       -> domain ports
            HarnessRuntime
            CollaborationBackend
            PublicationRegistry
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
  platform: PlatformBuildRef
  harness: AdapterRef + HarnessProfile
  instrumentation: InstrumentationProfile
  peer_tool_integration: PeerToolIntegrationProfile
  collaboration_measurement: CollaborationMeasurementProfile
  collaboration_profile_builder: ComponentRef
  collaboration: AdapterRef + CollaborationProfile
  publication_registry: AdapterRef + PublicationProfile
  compute: AdapterRef + ComputeProfile
  research: AdapterRef + ResearchProfile
  storage: AdapterRef + StorageProfile
  evaluator: AdapterRef + EvaluatorProfile
  enforcement: EnforcementProfile
  model: ModelProfile
  provider_selection: ProviderSelectionRecord
  budget: BudgetEnvelope
  budget_plan: BudgetPlanRef
  provider_receipt_verifier: ComponentRef
  actor_allocation: ActorAllocationPolicy
  campaign: CampaignRef
  peer_activation: PeerActivationPolicy
  measurement: MeasurementProtocol
  submission_policy: SubmissionPolicy
  observer_policy: read_only | none
  scale_acceptance: ScaleAcceptanceProfile?  # required for a large-fleet study
  block_plan: RandomizedBlockPlan
  analysis_plan: AnalysisPlanRef
  progression_rule: StudyProgressionRule?

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

PlatformBuildRef
  source_commit: commit
  build_artifact_digest: digest
  domain_schema_version: string
  composition_schema_version: string

EnforcementProfile
  experiment_runner: ComponentRef
  campaign_controller: ComponentRef
  artifact_service: ComponentRef
  submission_registry: ComponentRef
  budget_gateway: ComponentRef
  compute_broker: ComponentRef
  research_broker: ComponentRef
  sandbox: ComponentRef

ComponentRef
  component_name: string
  source_version_or_commit: string
  build_artifact_digest: digest
  configuration_digest: digest
  capability_or_schema_version: string

ProviderSelectionRecord
  rule_version: string
  candidate_snapshot_timestamp: timestamp
  candidate_snapshot_digest: digest
  qualification_workload_digest: digest
  observation_window: duration
  eligibility_profile_digest: digest
  cost_projection_profile_digest: digest
  tie_break: effective_cost_then_latency_then_provider_id
  selected_model_profile_digest: digest
  qualification_evidence_digest: digest

BudgetPlanRef
  plan_id: string
  campaign_run_id: string
  organisation_limit_usd_nanos: integer
  actor_allocations: map<actor_id, integer>
  rate_card_digest: digest
  source_digest: digest
```

The randomization algorithm assigns condition labels to already defined execution positions and stochastic seeds. `task_seed` generates one immutable material bundle for the whole block; its digest must be identical in all four resolved runs. Run and actor stochastic seeds control model/runtime randomness only and cannot alter missions, inputs, public workloads or evaluator data. The runner materializes and hashes every `BlockAssignment` before any outcome is observed. A resolved manifest cannot choose its condition, variant or seeds, and retries remain traceable to the original assignment.

`AnalysisPlanRef` freezes the registered block population, potential-outcome estimand, assignment mechanism and conditioning set, test statistic and studentization, one-sided alpha, power target and minimum complete-block count, confidence-bound inversion algorithm and numerical resolution, secondary contrasts and mechanical handling of defaults, missing outcomes and infrastructure-invalid blocks. For each block `b`, let `D_b` be the observed `peer_collab - peer_isolated` difference and let `B` be the number of complete registered blocks. The point statistic is `mean(D_b)` and the studentized statistic is `(mean(D_b) - tau_0) / (sd(D_b) / sqrt(B))`, with the zero-variance rule fixed before registration. The primary estimand is the finite-population mean causal effect of collaboration visibility over the execution positions conditioned into the two peer arms across the registered blocks.

Primary inference conditions on each block's task/materials, four execution positions and the unordered pair of positions assigned to the two peer conditions. The randomization distribution applies every independent `peer_collab`/`peer_isolated` label swap permitted within those pairs by the registered assignment algorithm. V0 computes the complete conditional distribution by exhaustive enumeration or an algebraically equivalent exact algorithm. For each candidate `tau_0`, the implementation imputes the compatible constant-additive sharp null, recomputes the studentized statistic over that distribution and tests the weak null that the mean effect is at most `tau_0`. Inverting these one-sided tests over a frozen grid/root-finding rule yields the lower confidence bound. The resulting weak-null claim is asymptotic or conservative under the registered regularity conditions, not finite-sample exact.

A separately reported finite-sample exact Fisher test uses the same conditional assignments for the sharp null that every conditioned peer execution position has identical potential outcomes under `peer_collab` and `peer_isolated`. The sharp and weak nulls, and their claims, are never conflated.

`StudyProgressionRule`, when present, freezes the evidence and budget gate for funding a separate higher-capability-model study. The Flash-to-Pro rule is registered before Flash outcomes are inspected, reports every attempted study and treats the Pro study as conditional model-profile evidence rather than pooling it with or retroactively replacing Flash.

`AdapterRef` records implementation name, semantic version or commit, configuration digest and declared capability version. `ComponentRef` records component name, source version or commit, build artifact digest, configuration digest and capability/schema version for an application or enforcement service. `PlatformBuildRef` binds the shared domain and composition code. The resolved configuration digest covers the platform, every adapter, every component in `EnforcementProfile`, all profiles and policies, and the campaign definition transitively; pinning only the OpenCode and backend versions is insufficient.

`ModelProfile` separately records model author and exact requested model identifier, gateway transport, serving provider and route, expected returned identity or fingerprint when available, endpoint class, inference parameters, retry/fallback policy, `BillingPolicy` and `ProviderCachePolicy`. Cache policy is one of `disabled`, `actor_run_scoped` or `provider_managed_observed`. A confirmatory peer comparison requires effective actor isolation through a provider namespace, a frozen non-semantic per-actor isolation prefix, or disabled caching; `provider_managed_observed` without isolation is calibration-only.

Before a study is registered, `ProviderSelectionRecord` freezes the candidate-route snapshot and digest, qualification workload and time window, eligibility thresholds, cost projection method, deterministic tie-break and resulting selected route. V0's rule first excludes routes that cannot attest the exact model, support the full request/tool surface, enforce the registered data and cache policy, expose sufficient billing evidence, or meet the declared reliability and latency floors. It then selects the lowest projected dollar cost for the frozen representative request mix, breaking an effective-cost tie by lower measured latency and then a fixed provider identifier. The qualification observes no treatment outcomes. Dynamic price or latency routing and provider fallbacks are disabled after selection.

`BillingPolicy` freezes currency, price-catalog source and digest, rate-schedule version, allowed price tiers or windows, provider timestamp source and the block rule. V0 uses `single_effective_tier_per_block`: all four positions in a block settle under one tier, and an unplanned catalog or tier transition triggers the registered whole-block invalidation/retry rule. A future study may instead preregister a balanced tier-by-position design. Every call records its provider timestamp, effective tier and cache-hit/cache-miss/output unit rates rather than inferring dollars from OpenCode telemetry.

`BudgetPlanRef` is the immutable authority for the campaign and actor limits.
For a registered run, the controller loads the plan only with the source digest
recorded in the resolved run manifest. The budget ledger may copy these values
for atomic admission, but it cannot redefine them. The separately pinned
provider-receipt verifier reconstructs identity, usage and billed cost from raw
provider evidence without consulting ledger-derived usage or charge fields.
Both authority digests are included in close-time reconciliation evidence.

The first registered four-condition study uses an exact `deepseek-v4-flash` profile after provider selection and a common pass/fail feasibility qualification verifies base tools, native handoffs, peer tools and a valid campaign artifact without inspecting treatment differences. If its preregistered promise trigger is met, a separately registered `deepseek-v4-pro` study may repeat all four conditions. This is model-profile replication evidence, not an in-study substitution: attempts are all reported, results are not pooled across models, and changing the model, transport, provider route or any other profile field always creates a new study version. A later provider replication holds the model and other factors fixed while changing only the provider profile. No domain port contains provider-specific types.

`InstrumentationProfile` freezes the OpenCode and SDK versions, out-of-process event adapter, event-schema version, buffering/backpressure policy and reconciliation policy. If an in-process plugin is required, it also freezes its commit, configuration digest and an API/hook allowlist. The plugin must be condition-blind and must not register transform hooks, model-request or tool-execution mutation hooks, tools, commands or any other behavior that can change prompts, parameters, permissions, scheduling or budgets. This restriction is established by pinned code review and conformance against the effective runtime configuration; it is not an assumed property of OpenCode plugins.

`PeerToolIntegrationProfile` separately freezes how the collaboration tool is exposed, including any plugin or MCP adapter commit, schema, description, permissions and configuration digest. It is identical in `peer_isolated` and `peer_collab`. Instrumentation cannot inject the peer tool; if OpenCode requires an in-process plugin for tool registration, that plugin belongs here and remains distinct from the observational plugin.

`CollaborationMeasurementProfile` freezes the definitions and denominators for reach, exchange, integration and overhead; event and artifact-lineage deduplication; attributable-token policy; the peer-condition scope and `not_applicable` rules; and the human-label codebook. It is observational, cannot affect candidate selection, task scores or the population included in the primary estimate, and is identical across conditions. Human function labels use `observed`, `not_observed` or `unclear` with evidence references and are assigned from a redacted view without access to hidden evaluation events or scores.

`organisation_size` is the sole source of truth for fleet size `N`. `PeerActivationPolicy` freezes the start barrier, job-delivery schedule, maximum concurrency, wake/resume behavior and deadline handling; its resolved session count must validate against `organisation_size`. V0 uses `eager_all`: exactly `N` peer sessions are created and receive every job. The first pilot sets `N = 4`; later studies may register other sizes. Within a study, `N` is identical across arms and blocks. The controller applies the same policy to `peer_isolated` and `peer_collab` without inspecting condition-specific content or runtime activity.

`MeasurementProtocol` freezes resource reset, image and dependency digests, warmup, repetition count and order, reference canaries, environmental recording, contamination tolerances and condition-blind retry or invalidation rules.

`ScoringProfile` is a separate campaign input. It freezes per-bucket latency
SLOs, point selection, failure and attainment gates, cross-bucket
normalization, repetition aggregation, reference evidence lineage, numerical
resolution and the improvement-bound rule. The evaluator scores observed API
outcomes and does not inspect or constrain a candidate's serving architecture
beyond the campaign's ordinary artifact, resource and benchmark-integrity
policy. A scoring-profile change creates a new campaign/study version without
changing the compute adapter.

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
  compute_schedule: DeterministicActorSchedule

DeterministicActorSchedule
  algorithm: name + version
  epoch: campaign-relative timestamp
  slots: ordered list<ActorSlot>
  unused_slot_policy: idle
  overrun_policy: terminate_at_slot_boundary
  terminal_release_policy: slot_boundary
  visible_evaluation_policy: owner_slots_and_quota
  hidden_evaluation_policy: separate_evaluator_schedule
```

Each `ActorSlot` binds an actor ordinal, start offset and fixed duration. For both peer conditions, the `N` identical per-actor caps exactly partition each relevant organisation cap, and every top-level actor receives matched slots and cannot consume another actor's allowance or time. Unused slot time idles and is never reassigned; work that cannot finish by its legal slot boundary is rejected before start or terminated under the frozen failure rule. Actor-visible compute results are withheld until the slot boundary, so another peer's demand cannot alter admission or observable release time. Public evaluation uses the submitting actor's slots and `cloud_gpu_seconds`; hidden post-closure evaluation uses a separately frozen evaluator schedule and account. `solo` and `native_multiagent` use the organisation envelope and their own preregistered schedule directly. This deliberately makes V0 a narrow test of information sharing. The `peer_collab - native_multiagent` contrast is a bundled comparison of operating approaches, because topology and allocation mechanics differ. Pooled or transferable allocation is a later registered treatment.

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
  status: pending | active | closed | invalid | failed
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

`close()` requires a configured `BudgetReconciliationGate`. It first stops the
harness and revokes its session credentials, waiting for authenticated in-flight
model requests to reach a durable terminal state. It then reconciles the
campaign ledger. The controller emits `campaign.closed` and returns a
`CampaignResult` only when no reservation is active, forfeited, overrun or
missing its required raw stream and provider-metadata receipts. Otherwise, it
emits `campaign.invalid`, marks the handle invalid and raises without returning
a scoreable result. A local fake that makes no model calls must still supply an
explicit `no_model_calls` reconciler; an absent gate cannot close a campaign.

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

`deliver` is idempotent for the same session, `job_id` and canonical materials digest. Repeating an interrupted fan-out completes missing deliveries without duplicating accepted work; reusing a `job_id` with a different materials digest fails closed.

The OpenCode adapter must:

- use the pinned OpenCode server, SDK and effective configuration in the manifest;
- use the out-of-process event adapter where sufficient and load only the observational plugin/API surface allowed by `InstrumentationProfile`;
- expose the peer tool only through the separately pinned `PeerToolIntegrationProfile`, identical in both peer arms;
- preserve top-level sessions and workspaces across jobs;
- create, start, deliver to, wake and stop peer sessions according to the same frozen activation policy in both peer conditions;
- deny native task/subagent calls outside `native_multiagent`;
- expose only pinned stock general-purpose subagents in `native_multiagent`;
- add no experiment-owned planner, supervisor, merger, critique or retry loop;
- emit all available parent, child, message, tool, timing and usage events;
- export the effective configuration and complete available session tree;
- stop every live session when the campaign closes.

Harness events are observational. The instrumentation plugin is condition-blind and must not alter prompts, parameters, tools, permissions, scheduling or budgets; conformance verifies effective prompt, model, tool and permission digests and rejects unapproved OpenCode hooks. The adapter does not enforce the authoritative dollar limit, filesystem boundary, network policy or external capability quota.

## Artifact service

```text
interface ArtifactService:
  snapshot(session_transport, workspace_paths, metadata) -> ArtifactRef
  materialize(session_transport, source: ArtifactRef | PublicationId, destination) -> MaterializationReceipt
  publish(session_transport, idempotency_key, body, reply_to?, artifact_refs?: list<ArtifactRef>) -> Entry
  authorize_owned(trusted_service_context, session_transport, ref: ArtifactRef, purpose) -> OwnedArtifactAuthorization
```

This application service is the only general-purpose agent-facing path for
moving bytes between a workspace and `StorageBackend`; compute, evaluation and
quarantine extraction use separately authorized internal paths. `snapshot` and
`publish` derive campaign, actor, workspace root and collaboration scope from
the session; no caller-supplied scope or root can widen access. `snapshot`
canonicalizes bounded workspace paths, rejects symlink escape and writes an
immutable actor-owned artifact. `materialize` writes only into the caller's
session-bound workspace after verifying either ownership of an `ArtifactRef`
or audience access to a `PublicationId`.

For publication, the service proves ownership, prepares a durable opaque record in `PublicationRegistry`, places that identifier in a collaboration entry, and binds the record to the successfully written `EntryId`. A failed collaboration write is aborted; an unbound or aborted identifier never resolves. Every materialization is reauthorized from the caller's authenticated session, after which the service creates a purpose- and artifact-bound `ArtifactReadAuthorization` for the trusted storage read. The identifier is not a bearer capability. Preparation, entry creation and binding use one idempotency key; recovery either binds the matching durable entry or aborts the preparation, never invents a second publication. The service contains no recommendation, task-allocation or artifact-merging policy.

`authorize_owned` is an internal application-service call available only to pinned services such as `SubmissionRegistry` and `ComputeBroker`; the trusted-service credential is never mounted in an agent sandbox. It derives actor and campaign from `session_transport`, verifies an ordinary admitted artifact and returns a server-held authorization bound to that actor, artifact and purpose. `OwnedArtifactAuthorization` specializes `ArtifactReadAuthorization`; for the `candidate_lifecycle` purpose the registry persists it with the immutable candidate record so public and selected hidden evaluation can read exactly that artifact. It is never returned through the agent-facing receipt. The method rejects `PublicationId`, `QuarantinedArtifact`, cross-campaign references and artifacts owned by another actor.

`ArtifactRef` denotes an admitted ordinary artifact. `QuarantinedArtifact` is a distinct type and is rejected by snapshot, materialize, publication, compute staging, submission and evaluation. Only a campaign-approved isolated renderer or extractor may consume a quarantined object; after applying its declared checks, it may create a new `SanitizedDocument` or ordinary `ArtifactRef` with lineage back to the quarantine digest.

Snapshot metadata may include `ArtifactProvenance`, a list of parent ordinary artifacts and publications. An owned parent must belong to the same actor and campaign. A cross-actor parent must be a bound publication that the actor was authorized to materialize, and the materialization must appear in the ledger. Invalid parents are rejected. Provenance is optional and authorization-validated, but it remains a conservative declaration of possible influence rather than proof that the parent semantically changed the result.

## Publication registry port

```text
interface PublicationRegistry:
  prepare(service_context, publication_key, campaign_run_id, owner_actor, artifact: ArtifactRef, audience) -> PublicationId
  bind(service_context, publication_id, entry_id: EntryId) -> void
  abort(service_context, publication_id, reason) -> void
  resolve(service_context, publication_id) -> PublicationRecord
  export(campaign_run_id) -> PublicationSnapshot
  reset(campaign_run_id) -> void
```

`PublicationRecord` contains the publication key, campaign, owner, ordinary artifact, audience, bound entry, status and audit metadata. `prepare` is idempotent for an identical publication key and arguments and rejects key reuse with different arguments; `bind` is idempotent only for the same entry. `resolve` returns only a bound active record and fails closed for prepared, aborted, unknown or cross-campaign identifiers. Registry state is durable across campaign snapshot/resume and exported with the evidence bundle. The registry is server-only and never reads artifact bytes or accepts an agent session.

## Collaboration backend port

```text
interface CollaborationBackend:
  provision(campaign_run_id, visibility: none | actor_private | organisation_shared) -> CollaborationScope
  publish(scope, actor_context, idempotency_key, body, reply_to?, publication_ids?) -> Entry
  list_recent(scope, actor_context, cursor?, limit?) -> Page<Entry>
  get_thread(scope, actor_context, entry_id) -> list<Entry>
  search(scope, actor_context, query, cursor?, limit?) -> Page<Entry>
  notifications(scope, actor_context, cursor?, limit?) -> Page<Notification>
  export(scope) -> CollaborationSnapshot
  reset(scope) -> void
```

The server derives campaign and actor identity from authenticated tool context; agent-supplied identifiers are ignored. `publish` is idempotent for the same scoped key and canonical request and rejects key reuse with different content, allowing `ArtifactService` to recover safely across publication preparation, entry creation and registry binding.

Visibility rules:

- `solo` and `native_multiagent`: `none`; the tool is unavailable.
- `peer_isolated`: `actor_private`; the peer-tool schema is present but each actor can observe only its own entries and referenced artifacts.
- `peer_collab`: `organisation_shared`; every top-level peer in the organisation can observe shared entries and artifacts.

The two peer modes must use the same implementation version, persistence rules, pagination, tool descriptions and operation limits wherever truthful. Authorization checks—not client filtering—enforce visibility.

Pagination cursors are signed and bound to the scope, authenticated actor,
operation and normalized query. Notification polling returns a durable
watermark cursor even when the reader is caught up, so a later poll cannot
repeat previously returned notifications. In `actor_private`, returned entry
sequences, pagination positions and notification watermarks are actor-local;
campaign-wide activity is never encoded in an actor-visible value.
Authorization denials, including invalid cursor signatures, commit an audit
event before the denied operation returns its error.

Collaboration visibility is the only cross-actor publication path in the peer comparison. Candidate and evaluator feedback, broker results, queue metadata, caches, files and artifacts remain actor-private until their owner explicitly publishes content or an authorized `PublicationId` in `peer_collab`.

`PublicationId` is opaque and resolves only inside `ArtifactService` through `PublicationRegistry`. The identifier alone grants no authority: the registry must contain a bound active record and every read is checked against the caller's server-derived campaign and the recorded audience. A deliberately published identifier therefore resolves for an authorized same-campaign peer, while guessing, replaying or exposing an unbound identifier fails. An `actor_private` scope cannot create an organisation-audience publication. This preserves explicit sharing without exposing transferable signed grants to agents or coupling the storage and collaboration adapters.

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
  reserve_visible_evaluation(trusted_service_context, owned_artifact_authorization, evaluation_spec) -> EvaluationReservation
  reserve_hidden_evaluation(trusted_service_context, candidate_artifact_authorization, evaluation_spec) -> EvaluationReservation
```

`ComputeBackend` knows how to operate a cloud GPU, local fake or replay target. It does not know the experimental condition, agent prompts or campaign outcome.

The executable development adapter refines external execution into three
narrow ports: durable `ComputeBackend` orchestration, a
`ComputeExecutionTransport` that dispatches and polls one provider call, and a
`ComputeEvidenceResolver` that independently resolves immutable result bytes.
Every request has a stable key and digest. The backend commits `registered` and
then `dispatching` before invoking the transport. A successful call advances to
`dispatched`; collection alone may advance it to terminal `complete` or
`failed`. A timeout leaves it `dispatched` and safely collectible.

`FrozenComputeRunManifest` is the external execution authority. It canonically
binds the campaign, exact request set, transport profile and backend profile.
The file is created once, loaded against an expected digest and re-read before
admission and reconciliation. Execution rows store its digest. At closure, the
backend reconstructs requests from the manifest and requires the ledger key set
to match exactly, so process restart does not require request replay.

An exception after dispatch begins has an unknowable side-effect boundary, so
the backend records `ambiguous` and never automatically dispatches that key
again. Only a transport-specific, positively identified pre-acceptance
rejection may become `failed` without an external call. Campaign reconciliation
rejects `registered`, `dispatching`, `dispatched` and `ambiguous` executions and
re-resolves every terminal evidence pointer. This policy favors invalidating a
development run over risking duplicate GPU spend or counting unverifiable work.

Collection, resolution and reconciliation also resolve the provider dispatch
record separately from the execution ledger. They verify its external call ID
and digest before trusting a terminal result. Observing another caller's
`registered`, `dispatching` or `dispatched` execution is nonterminal; it never
fails the candidate or its compute reservation. A caller may retry collection
under the same idempotency key.

The transport requires a single request-bound `ComputeSpendAuthorization`
before dispatch. A separately profiled authorization service durably issues the
authorization against explicit approval evidence and atomically consumes it
before the provider call. Its ledger binds the frozen run-manifest digest,
transport profile and exact request digest. Collection requires no new spend
authority because it can only resolve the recorded external call. Campaign
closure requires both budget and compute reconciliation gates. The no-compute
adapter accepts only a frozen manifest that disables compute and declares no
transport, backend or requests; it cannot be composed with a compute-enabled
registered run.

Observed function-body duration is retained uncapped. A value above the
reservation invalidates reconciliation instead of being clamped to the limit.
Function-body time is operational usage evidence, not an authoritative Modal
billing receipt; scored cost reporting requires separate provider billing
evidence.

`ComputeJobRequest` names actor-owned admitted `ArtifactRef` inputs, a declared command and expected output paths. `ComputeBroker` derives actor and campaign from the session transport, obtains purpose-bound ownership authorization from `ArtifactService`, rejects quarantined inputs, stages the inputs as a read-only `ImmutableInputBundle`, then enforces command allowlists, exclusive leases, concurrency and the actor's fixed GPU-time quota. The backend returns declared output files to the broker; the broker stores them as new actor-owned artifacts and exposes their references in `ExecutionResult`. Infrastructure credentials remain behind the broker. An actor can observe only its own handles and terminal coarse status; it cannot inspect another actor's request, schedule, cache state or output. `running`, queue position and estimated-start fields are intentionally absent.

In both peer conditions, each actor has the same fixed, non-transferable GPU-time
allocation and fixed-duration slots from the frozen
`DeterministicActorSchedule`, matched by actor ordinal across paired runs. An
actor's exploratory jobs and visible evaluations run only in its own slots and
both charge its `cloud_gpu_seconds` and the organisation envelope. Unused time
idles; jobs never borrow or shift another actor's slot. Before a candidate is
accepted, `SubmissionRegistry` uses `reserve_visible_evaluation` to reserve its
declared worst-case measurement duration. If the submitting actor lacks quota
or a legal slot, the provisional admission remains unaccepted and safely
retryable or is explicitly rejected by the registered recovery policy. Status
remains `accepted` until the scheduled release boundary even if backend
execution ends earlier. Thus actor-visible admission and release do not depend
on peer demand.

Only one agent experiment or evaluator measurement may hold a GPU lease at a time. Hidden measurements begin only after the agent-visible phase closes and follow a separate condition-blind evaluator schedule and measurement account; their GPU seconds are reported as study overhead, not treatment-budget consumption. Scored execution follows the `MeasurementProtocol`: restore the declared image and clean state, record relevant device and software state, perform fixed warmup, run the frozen repetitions in the frozen order, and bracket candidate measurements with reference canaries. Canary drift beyond tolerance triggers the predeclared retry or invalidation rule, never an ad hoc condition-specific decision. Each reservation, slot, reset, lease, repetition, canary, release and result-release boundary emits a receipt.

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
  describe_owned(scope, actor_context, ref: ArtifactRef) -> ArtifactDescriptor
  get_owned_artifact(scope, actor_context, ref: ArtifactRef) -> bytes
  get_artifact_for_service(scope, trusted_service_context, authorization: ArtifactReadAuthorization) -> bytes
  save_snapshot(scope, CampaignSnapshot) -> SnapshotRef
  export(scope) -> CampaignExport
  reset(scope) -> void
  seal(scope, final_manifest, checksums) -> StorageReceipt
```

`ArtifactStoragePolicy` is pinned in the resolved study manifest and sets
positive `max_artifact_bytes`, `max_actor_bytes` and `max_campaign_bytes`
limits, ordered from narrowest to broadest. Admission checks the immutable
content size against all three limits in one serialized metadata transaction;
quota failure leaves no admitted record or blob. Before admission, storage
durably registers the complete campaign actor roster and requires the campaign
limit to cover the sum of every actor's full nontransferable allocation. It
rejects roster or policy changes across restart. First registration fails if
the campaign already contains pre-roster artifacts; those bytes require an
explicit migration rather than implicit allocation. Reopen also validates that
every stored owner belongs to the roster and that existing artifact, actor and
campaign usage fits the registered policy. Therefore one actor exhausting its
allocation cannot reduce another actor's capacity. The adapter also pins
the trusted service-to-read-purpose allowlist. A service transport is unique
while active, and an artifact-read authorization is bound to that exact
transport, artifact and purpose rather than only to a reusable service name.

The V0 local adapter may use JSONL, SQLite and content-addressed files. It guarantees monotonic event sequence numbers, immutable artifact hashes, atomic snapshots and campaign-scoped export.

The executable local slice implements a race-resistant single-file subset of
workspace snapshot and materialization. It traverses every directory through
non-following file descriptors, accepts only regular files and creates outputs
without replacement. The artifact service derives the workspace root from the
authenticated session's server-side binding; an agent cannot supply or change
that root. Campaign sealing revalidates all blob sizes and digests, binds a
canonical final manifest and rejects every subsequent artifact write.

Storage records evidence but does not infer validity or enforce budgets. Its authorization layer enforces campaign and owner visibility; guessing a digest does not reveal existence or metadata. Agent-facing reads and writes use `ArtifactService`. `describe_owned` and `get_owned_artifact` require the server-derived owner. `get_artifact_for_service` accepts only a non-exportable trusted-service identity plus a server-held `ArtifactReadAuthorization` bound to campaign, artifact, purpose and authorization decision; it records the read and rejects use outside that binding. `ArtifactService` issues such authorization only after an ownership or active-publication audience check. Storage never accepts `PublicationId`, never promotes visibility automatically and never exposes this service path to an agent sandbox.

## Evaluator port

```text
interface Evaluator:
  capabilities() -> EvaluatorCapabilities
  validate(candidate: ArtifactRef, variant: VariantRef) -> ValidationResult
  visible_evaluate(candidate, reservation?, evaluation_key) -> EvaluationReceipt
  hidden_evaluate(candidate, reservation, evaluation_key) -> EvaluationReceipt
  resolve(receipt, candidate, reservation?, scope) -> EvaluationResult
```

The evaluator receives immutable artifacts, frozen variant data and an
evaluation resource lease. It does not receive the experimental condition,
agent identities or collaboration trace. Evaluation jobs are idempotent by a
server-derived key. Results remain in an evaluator-owned evidence ledger; the
submission registry stores only opaque receipts. Receipt resolution rechecks
the evaluator profile, candidate digest, scope, reservation binding and result
evidence against evaluator authority rather than trusting a score document in
the submission database.

Visible evaluation may be invoked only through the submission service and an actor-owned `EvaluationReservation`. Its worst-case GPU duration is reserved before candidate admission, runs in the submitting actor's deterministic slots, charges the same actor and organisation `cloud_gpu_seconds`, and is released at the frozen slot boundary. Its result is visible only to the submitting actor unless that actor explicitly publishes it in `peer_collab`. Hidden evaluation is authorized only after submission closure and final selection and uses a separate evaluator reservation/account. Evaluator measurements on shared hardware use exclusive compute leases and the same frozen reset, warmup, repetition and canary protocol.

The executable split-scope adapter exposes one registered evaluator identity
while binding visible and hidden work to distinct underlying evaluator profiles,
workload digests, compute accounts, schedules and evidence namespaces. Its
durable outer receipt binds the scope, candidate, reservation, lane and
underlying evaluator receipt. Scope-specific evaluation keys fail before
dispatch if they use the wrong namespace. This composition contract does not
by itself qualify either underlying compute lane or materialize hidden data.

## Submission registry

```text
interface SubmissionRegistry:
  initialize(campaign_run_id, job_id, reference_artifact, reference_visible_receipt, policy) -> void
  submit(session_transport, job_id, artifact: ArtifactRef, metadata) -> CandidateReceipt
  visible_result(session_transport, receipt) -> pending | VisibleResult | EvaluationFailure
  close(campaign_run_id, job_id?) -> SubmissionSet
  select(submissions: SubmissionSet) -> SelectionResult
  evaluate_hidden(selection_receipt: SelectionReceipt, reservation) -> HiddenResult
```

The agent-facing submission methods derive campaign and actor from the session
transport and verify that `job_id` belongs to that campaign. The registry calls
`ArtifactService.authorize_owned` for submission and rejects a `PublicationId`,
quarantined object, unowned artifact or cross-campaign reference. Because the
submission and compute ledgers are independent adapters, admission uses a
durable state machine instead of claiming a cross-database atomic transaction.
It first commits an immutable provisional candidate, obtains an idempotent,
artifact-bound compute reservation, and then marks the candidate admitted.
Retries recover the same provisional candidate and reservation, including a
cancelled compensation record. Closure rejects provisional candidates and
orphaned reservations. Submissions are immutable. `visible_result` returns
`pending` until the registered release boundary even if evaluation completes or
fails early. Candidate existence, artifact metadata and public feedback are
actor-private while work remains open; `peer_isolated` actors cannot enumerate
or infer one another's submissions. Each peer has the same fixed,
non-transferable submission and compute allowance, so another actor cannot
consume its capacity or cause a quota rejection. The registry may see all
submissions for neutral post-closure selection but never turns aggregation into
an agent-visible channel.

Limits and the neutral selector are identical across conditions. Each peer
submission must stand alone; the registry never merges artifacts. The registry
recomputes the neutral selection from evaluator-owned receipts, persists it,
and returns an opaque `SelectionReceipt`. Hidden evaluation accepts only that
receipt and recomputes the selection before reserving hidden compute.
`SelectionResult` contains either the best eligible candidate under the
campaign's normalized public ordering and deterministic tie-break, or its
declared reference outcome. The reference is a registered immutable artifact
with a separate visible evaluation receipt. Whether a candidate or the
reference wins, the selected artifact always receives a new hidden evaluation;
visible and hidden scores are never treated as interchangeable. The same
fallback and selector over `peer_isolated` and `peer_collab` are required for
the communication estimand.

The executable fake slice persists provisional admission, candidate-to-compute
bindings, evaluator receipts and authoritative selections. It refuses closure
unless every visible reservation corresponds to exactly one admitted candidate
and has a matching terminal evaluation state. It withholds completed results
until the actor's explicit release boundary, selects only after closure and
uses a separately accounted hidden reservation for every selected artifact.
The fake evaluator owns a separate durable receipt ledger and deterministically
reconstructs its evidence during receipt resolution. This proves orchestration,
retry and integrity behavior without claiming that the fake evaluator is an
untrusted compute backend.

## Budget gateway

```text
interface BudgetAccount:
  provision_actor(actor_context, allocation: ActorBudgetAllocation) -> ActorBudgetHandle
  reserve(session_transport, request_context: ModelCallContext, maximum_usd) -> Reservation | Rejected
  settle(reservation_id, ProviderUsage) -> Charge
  release(reservation_id, reason) -> void
  snapshot() -> BudgetSnapshot
  reconcile(campaign_run_id) -> BudgetReconciliation
```

Enforcement rules:

- Every model credential available to a campaign terminates at the gateway.
- Sandbox network policy denies direct model-provider egress.
- Primary, peer, subagent, retry, compaction and auxiliary model calls remain under one organisation account.
- In both peer conditions, each top-level actor is additionally constrained by the same fixed, non-transferable suballocation. A request is admitted only if both its actor allocation and organisation envelope can cover the reservation.
- Reservations use decimal arithmetic, conservative maximums and the frozen `BillingPolicy` catalog, rate schedule and effective price tier.
- Requests that could exceed the cap are rejected before provider execution.
- The raw provider stream and its correlated generation-metadata receipt are retained unchanged and reconciled after settlement. The gateway uses the provider's exact billed total for settlement when present; the frozen rate card supplies the conservative pre-call reservation bound.
- Every response records requested and returned model identifiers, available revision and system fingerprint, provider request ID, provider timestamp, cached input/output accounting, effective cache policy, price tier and cache-hit/cache-miss/output unit rates.
- Gateway-controlled caches use separate `(campaign, actor)` namespaces in both peer conditions and are never shared across actors or campaign runs or selectively prewarmed by condition. Provider-managed isolation keys or a frozen non-semantic per-actor isolation prefix are used where needed; a provider that cannot provide effective isolation is calibration-only.
- Subscription-backed model access is forbidden in confirmatory studies.
- Token revocation is a quiescence barrier: it rejects new authentication and
  waits for already authenticated calls to settle, release or forfeit before
  returning.
- Close-time reconciliation verifies reservation and charge counters and
  rejects active reservations, forfeitures, overruns, unknown terminal states
  or settled calls missing either required raw receipt. It also compares all
  persisted limits and allocations with the manifest-pinned `BudgetPlan`, then
  independently reconstructs each usage digest and charge through the pinned
  provider-receipt verifier. The ledger and its audit table are evidence, not
  their own trust anchor; a coherent multi-table rewrite must fail closure.

OpenCode's cost and token fields are retained as observational telemetry. They are reconciled against the gateway but never authorize requests, extend the cap or replace provider-accounting evidence.

V0 represents USD internally as integer nanodollars and rates as integer
nanodollars per million tokens. Ceiling division makes reservation and
settlement fixed-point and conservative without binary floating-point values.
This is the executable representation of the registered decimal amounts; user-
facing dollar strings are derived only for reporting.

The gateway derives campaign, actor and harness session from the same non-exportable session transport used by the brokers. `ModelCallContext` identifies only the call itself. Model-supplied or agent-supplied identity headers cannot select another actor's account or cache namespace.

The runtime credential lifecycle is two-phase: the gateway issues a pending
opaque credential for a provisioned actor, activates it only after the harness
returns the actual session identifier, and revokes it on creation failure,
suspend, stop or rollback. A pending or revoked credential cannot invoke the
provider route.

An actor may observe only its own charges, remaining allocation and rejections. Because the peer allocations partition the organisation envelope and unused capacity is not reassigned, another peer's activity cannot cause an actor's model call to be admitted or rejected in V0.

The study declares how identity and billing drift are handled before execution. A persistent returned model, fingerprint, price catalog or rate-schedule change creates a new `study_version`; an unplanned identity, catalog or price-tier change within a randomized block invokes the frozen whole-block invalidation/retry rule. Missing provider identity or billing fields are recorded explicitly rather than inferred.

## Sandbox policy

The harness runtime launches inside an enforced sandbox profile that declares writable paths, process limits, network destinations and per-session broker endpoints. It mounts the session-bound local transport but no bearer, provider, cloud or storage credential. Raw network egress is denied; live research is available only through the broker's SSRF-resistant fetch path, and quarantined artifacts are neither readable nor executable until an explicit safe-extraction policy admits derived output.

The sandbox must prevent bypassing the budget gateway or capability brokers. Logging an attempted bypass without blocking it is a control failure.

The sandbox is supplied through a `ProcessSandbox` port rather than built into
`OpenCodeHarnessRuntime`. It validates that the configured model endpoint is
the loopback gateway, wraps the complete runtime process tree and exposes a
pinned profile digest. Harness snapshot schema `opencode-harness-snapshot/v3`
retains that digest, and resume fails if it changes. The current macOS
`sandbox-exec` development adapter enforces only a loopback-wide network
boundary: every loopback port remains reachable, and filesystem and
process-resource constraints are not enforced. It therefore proves direct
provider egress denial but is not a registered implementation of the full
contract above. Scored execution requires a separate adapter or containment
layer that restricts accessible local services and enforces the declared
filesystem and process-resource limits. Every operating system requires its
own equivalent kernel- or container-level conformance proof.

## Collaboration profile builder

```text
interface CollaborationProfileBuilder:
  build(
    measurement: CollaborationMeasurementProfile,
    collaboration_trace: CollaborationTraceView,
    selected_artifact: ArtifactRef | null,
    blinded_function_labels: list<FunctionLabel>
  ) -> CollaborationUseProfile

CollaborationTraceView
  peer-surface events + validated publication/materialization events + selected-artifact provenance
  excludes hidden-evaluation events, outcomes, scores and evaluator-private state

FunctionLabel
  function: division_help | unsolicited_assistance | reuse | challenge_checking | deconfliction | specialization
  value: observed | not_observed | unclear
  evidence_event_ids: list<EventId>

CollaborationUseProfile
  applicability: applicable | not_applicable
  reach: shared_publishers + peer_retrievers + possible_actors + directed_links + possible_directed_links
  exchange: unique_entries_returned + replies + peer_artifacts_materialized
  integration: selected_artifact_owner + peer_source_actors + validated_cross_actor_lineage_edges | not_applicable
  functions: division_help + unsolicited_assistance + reuse + challenge_checking + deconfliction + specialization
  overhead: tool_calls + bytes_read + bytes_written + attributable_context_tokens? + service_latency
```

The builder is a read-only post-run analysis service. A trusted redactor derives `CollaborationTraceView` from the sealed evidence bundle before human labeling or profile construction; it is unavailable to agents and evaluators. Mechanical fields are computed before joining the profile to hidden outcome scores. It reports raw counts with denominators and never emits a weighted collaboration score. The profile applies to `peer_isolated` and `peer_collab`; it is `not_applicable` for `solo` and `native_multiagent` until a separate native-handoff mapping is registered.

A shared publisher authors at least one organisation-shared entry; a peer retriever receives or materializes another actor's content. A directed edge exists only when an entry or publication from one actor is returned or materialized for another actor. Each reader–entry and reader–publication pair counts once; retries, denials and merely available broadcasts do not count. A reply is a unique reply to a peer-authored entry. Integration includes the selected artifact's owner plus unique owners in its recursively validated `ArtifactProvenance`; peer-source actors are reported separately, and the whole field is `not_applicable` when no eligible artifact is selected. Provenance is explicitly a lower-bound proxy for semantic integration.

## Event model

Minimum event kinds include:

- campaign and job lifecycle;
- harness session lifecycle and native handoffs;
- model reservations, settlements and rejections;
- requested/returned model identity, fingerprint, cache usage, billing tier/unit rates and provider receipts;
- tool calls and results;
- collaboration reads and notifications with reader, operation, returned entry IDs, source actors, thread/reply IDs and payload bytes; writes and authorization denials;
- artifact-publication preparation, binding, abort, resolution and denial;
- publication materialization with reader, publisher and `PublicationId`; artifact snapshots with validated provenance parents;
- compute reservations, actor slots, leases, resets, repetitions, canaries, releases and fixed result-release boundaries;
- compute and research broker admissions, actor-visible status, results and denials;
- candidate submissions, public evaluation, selection and hidden evaluation;
- snapshots, infrastructure errors and validity decisions;
- human post-run observations.

Events and OpenCode traces are evidence. The corresponding gateway, sandbox, authorization or broker remains the enforcement authority.

## Conformance requirements

Before the first confirmatory pilot, tests must prove:

- A fake `HarnessRuntime` can run the core lifecycle without importing OpenCode.
- The platform build, application services, every adapter and every gateway, broker and sandbox component resolve to the registered source/build/configuration digests; mutating any one changes `resolved_configuration_digest` and invalidates the run.
- The registered block plan deterministically produces the same resolved run manifests, and no run may alter its assigned condition, variant, task seed or stochastic seeds.
- All four arms in a block receive byte-identical canonical task-material digests; changing a run or actor stochastic seed cannot change that digest.
- The analysis implementation reproduces the registered within-block assignment permutations, studentized weak-null statistic and confidence bound; its finite-sample exact label is used only for the separate Fisher sharp-null test.
- Every condition and block uses the exact registered model profile; a capability-check failure cannot trigger an in-study model substitution.
- The pinned observational event adapter and any instrumentation plugin are identical across arms and condition-blind. Static/API-surface checks reject transform, request/tool mutation, tool-registration and command-registration hooks; effective prompt, model, tool, permission and runtime-configuration digests match the registered condition construction. Sequence, loss detection, bounded buffering/backpressure and gateway/tool reconciliation pass under forced load.
- OpenCode sessions and workspaces survive delivery of a second job in the same campaign.
- `organisation_size` is the only fleet-size input; each condition derives and validates its allowed live-session count from it.
- Native handoffs work only in `native_multiagent` and respect `N`.
- Both peer modes eagerly activate exactly `N` sessions under the same start barrier, delivery schedule, concurrency and deadline rules.
- `peer_isolated` and `peer_collab` use the same pinned `PeerToolIntegrationProfile` and expose the same peer-tool schema, description and permission surface.
- Replaying the same redacted `CollaborationTraceView` through `CollaborationProfileBuilder` produces the same mechanical profile; retries and denials are excluded, and `actor_private` produces zero shared publishers, peer retrievers, cross-actor exchange and lineage edges.
- The redactor excludes hidden-evaluation events, outcomes, scores and evaluator-private state; `solo` and `native_multiagent` return `not_applicable`, and a peer run without an eligible selected artifact returns `not_applicable` for Integration.
- Collaboration-read events identify exactly which entries and publications were returned to which actor; a broadcast that no peer retrieves creates no directed edge.
- Reply counts include only unique replies to peer-authored entries.
- Cross-actor artifact-provenance parents require a bound publication and matching authorized materialization event; invalid lineage is rejected and selected-artifact source breadth is reproducible.
- Cross-actor collaboration-entry reads fail in `peer_isolated`; explicitly published entries and authorized publication identifiers succeed within the organisation in `peer_collab`.
- Publication records survive snapshot/resume and export. Retrying the same canonical publish after a crash produces the same entry and publication records; key reuse with different content fails. Prepared-but-unbound, aborted, guessed, unpublished, wrong-audience or cross-campaign identifiers fail closed; a deliberately bound identifier resolves for its authorized audience.
- Storage owner reads reject another actor, while its trusted-service read accepts only a valid purpose- and artifact-bound authorization from the pinned service and rejects agent calls, replay and mismatched campaign/purpose.
- Cross-actor candidate discovery, public feedback, broker jobs/results, queue metadata, caches and artifact reads fail in `peer_isolated`; in `peer_collab`, they remain private until explicitly published through the service.
- Cross-campaign collaboration, artifact, research-cache and event reads fail.
- All model sessions remain under one organisation envelope and cannot reach the provider directly; each relevant set of `N` peer suballocations exactly partitions its organisation cap, and peer actors cannot exceed, transfer or observe one another's allocation.
- Copying or replaying tool arguments, request handles, publication identifiers or model headers from another session never changes the broker-derived campaign or actor or bypasses publication-audience checks; the sandbox exposes no transferable service credential.
- Authenticated artifact snapshotting rejects paths outside the caller's workspace and creates immutable artifacts owned by the server-derived actor; materialization cannot escape the caller's workspace.
- Submission ownership and feedback visibility derive from the authenticated session, not caller-supplied actor or campaign fields; submission rejects a publication identifier, quarantined object and any ordinary artifact not owned by that actor.
- Compute and research calls cannot bypass their brokers or quotas.
- Compute staging rejects unowned or mutable inputs, mounts the accepted bundle read-only and records declared outputs as new artifacts owned by the submitting actor.
- `QuarantinedArtifact` is rejected by artifact publication/materialization, compute, submission and evaluation; only the approved isolated extraction path can produce a separately admitted artifact with recorded lineage.
- The deterministic schedule supplies matched fixed-duration slots by actor ordinal in both peer arms. Unused time idles, cross-actor borrowing fails, and callers receive terminal coarse status/results only at their own fixed release boundary regardless of peer demand.
- Candidate admission uses a recoverable provisional-to-reserved state machine
  to bind the public evaluator's worst-case GPU duration from the submitting
  actor. Exploratory compute and public evaluation use only that actor's slots
  and quota. Hidden evaluation starts after closure under a separate evaluator
  schedule/account.
- GPU measurements hold exclusive leases and deterministically apply reset, warmup, repetition and canary rules.
- Controlled web access rejects redirect and DNS-rebinding attempts to private or metadata addresses, sanitizes allowed text, and keeps binary or active content quarantined unless an approved isolated extractor produces separately admitted output.
- Every settled provider response is reconciled with observational harness telemetry.
- Campaign closure cannot return a scoreable result when budget reconciliation
  finds an active reservation, forfeiture, overrun, missing receipt or ledger
  counter inconsistency.
- Returned provider identity, fingerprint, cache accounting, provider timestamp, effective price tier and unit rates are captured; identity or billing drift applies the frozen block-validity rule.
- Hidden evaluator inputs and outputs never enter agent-visible storage.
- The organisation-level candidate total, campaign-specific default outcomes and neutral public-score selection are identical across all four conditions; the two peer arms also use identical fixed actor sublimits.
- A campaign definition can swap collaboration, publication-registry, compute, research, storage and evaluator adapters without source changes.

Before a large-fleet study, the manifest's `ScaleAcceptanceProfile` must be satisfied using a fake runtime. It supplies numeric thresholds rather than architecture constants: simulated actors (at least 100 for the first scale gate), startup duration, controller peak memory, event-lag percentile, dropped-event count, maximum page size, broker-admission latency, campaign-export duration and export-loss allowance. The test must also demonstrate independent cursors, deterministic slot isolation, bounded backpressure and no all-to-all polling.

## Change policy

Platform builds; application, adapter and enforcement component references; instrumentation, collaboration-measurement and peer-tool integration profiles; publication policies; capability manifests; model, cache and billing profiles; common instructions; tool schemas; sandbox policies; budgets; actor allocations and slot schedules; block assignments; scale thresholds; analysis plans; campaign definitions; evaluators; and selection rules may change during calibration. After registration, any such change requires a new `study_version` and a new complete set of condition runs.
