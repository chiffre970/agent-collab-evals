# Architecture

## Decision and scope

V0 is a modular experiment system for testing agent collaboration. It is not an early implementation of the full governed-work-network product.

OpenCode is the first coding-agent runtime adapter. It supplies the agent loop, context management, tools and native multi-agent handoffs, but it is not a dependency of the core experiment domain. V0 uses a dated DeepSeek model through a provider-neutral model gateway. The first registered four-condition study uses `deepseek-v4-flash`; a separately preregistered `deepseek-v4-pro` study is funded only under the recorded progression rule and never replaces Flash inside a study. Model author/version, gateway transport and serving provider are distinct registered factors. A pre-outcome selection rule chooses one eligible provider route for a study, after which the study freezes its endpoint, requested and expected returned identity, inference configuration, billing schedule and price-tier policy across every condition and block with fallbacks disabled. Switching model, transport or provider creates a new study version whose result is not pooled with the earlier configuration. These choices remain manifest data rather than core-domain dependencies.

V0 pins and instruments stock OpenCode rather than forking it, and implements the smallest collaboration service needed for the experiment directly behind `CollaborationBackend`. Hugging Face Agent Collabs is a candidate substrate to assess against the same contract if the custom backend misses its timebox, not an automatic fallback. It also remains a possible external replication target. The rationale and decision gate are recorded in [ADR 0001](decisions/0001-agent-runtime-and-collaboration-substrate.md).

The initial study has four conditions:

| Condition | Session topology | Collaboration visibility |
| --- | --- | --- |
| `solo` | One primary session | None |
| `native_multiagent` | One primary plus at most `N - 1` native child sessions | None |
| `peer_isolated` | Exactly `N` equivalent primary sessions | Actor-private only |
| `peer_collab` | Exactly `N` equivalent primary sessions | Organisation-shared |

The two peer conditions use the same fleet topology, collaboration tool schema, candidate policy and aggregate limits. The collaboration backend scopes entries to the originating actor in `peer_isolated` and to the organisation in `peer_collab`. This makes `peer_collab - peer_isolated` the communication estimand. The fleet size is manifest-configurable and frozen within a study; the first pilot uses `N = 4`, while later studies may scale it. Comparing `peer_collab` with `native_multiagent` is a useful bundled system comparison, not a clean topology-only estimand, because their allocation and delegation mechanisms differ.

## Design principles

1. **Keep the domain independent of OpenCode.** Core orchestration depends on a `HarnessRuntime` port; OpenCode is the pinned V0 adapter.
2. **Make campaigns durable.** An organisation's identities, sessions, private workspaces and allowed shared state persist across incoming jobs until its campaign closes.
3. **Change the smallest possible treatment surface.** The peer arms use identical, frozen activation, per-actor allocations and scheduling rules and differ only in collaboration visibility and the minimal instructions that accurately describe it.
4. **Treat the organisation as the comparison unit without creating peer side channels.** All work remains under one hard organisation envelope; each peer receives an identical fixed, non-transferable suballocation in V0.
5. **Separate domain packs from infrastructure.** A campaign declares jobs, required capabilities, submissions and outcome definitions; independent adapters provide harness, collaboration, compute, research, storage and evaluation.
6. **Separate observation from enforcement.** Runtime traces and ledgers measure behavior. Gateways, sandboxes, authorization checks and capability brokers constrain behavior.
7. **Keep hidden evaluation outside the agent environment.** Agents cannot read hidden workloads, scores or data from other conditions.
8. **Reset at campaign boundaries.** Nothing crosses conditions, replicates or campaigns unless a later study explicitly makes prior organisational history a treatment.

## System shape

```text
StudyManifest
     |
ExperimentRunner
     +-- CampaignController ----- durable organisation + incoming jobs
     +-- ArtifactService -------- workspace snapshots + authorized publications
     +-- SubmissionRegistry ----- owned candidates + public-eval admission
     +-- CollaborationProfileBuilder -- post-run descriptive profile
     |
     +-- HarnessRuntime --------- OpenCode adapter
     +-- CollaborationBackend --- private/shared namespace adapter
     +-- PublicationRegistry ---- durable publication authorization records
     +-- ComputeBackend --------- cloud GPU or fake adapter
     +-- ResearchBackend -------- off, frozen corpus or controlled web
     +-- StorageBackend --------- events, snapshots and artifacts
     +-- Evaluator -------------- serving evaluator or another task adapter
     |
     +-- BudgetGateway ---------- enforced model API metering and cutoff
     +-- CapabilityBrokers ------ enforced compute/research policy and quotas
     +-- Sandbox ---------------- enforced process/filesystem/network boundary
```

The composition root selects concrete adapters from the manifest, validates their declared capabilities and injects them into the runner. Adapters do not discover or instantiate one another.

## Core lifecycle

### Study

A study freezes the platform source/build digest; every application-service, adapter and enforcement-service version and configuration; the observational instrumentation, collaboration-measurement and separate peer-tool integration profiles; the exact model, billing and provider-cache profile; the campaign definition; fleet size; peer activation policy; organisation envelope; per-actor allocations and deterministic slot schedule; measurement protocol; analysis plan; and complete randomized block schedule. The resolved configuration digest covers this entire transitive composition. Each block freezes one task seed and material digest shared by all four conditions. Each run and actor receives a distinct stochastic seed that may affect model behavior but never task materials. The schedule assigns conditions to predeclared execution positions before any outcome is observed; retries remain linked to the original assignment.

### Campaign

A campaign is the lifetime of one condition-assigned organisation. At campaign start, the controller creates:

- stable agent identities;
- persistent harness sessions and session trees;
- one private workspace per top-level agent;
- the condition-scoped collaboration namespace;
- the organisation envelope and, for peers, fixed actor budget and capability allocations;
- campaign-level event and artifact storage.

The controller may then deliver one or more jobs. Sessions and workspaces remain live or resumable between jobs. Campaign state closes only after the job sequence, aggregate deadline or resource envelope ends.

For the two peer conditions, V0 uses one predeclared activation policy. The initial policy eagerly creates exactly `N` top-level sessions, delivers the same job to every session, and applies the same start barrier, concurrency limit, wake/resume rules and deadline. The policy is condition-blind and cannot inspect collaboration content. A later study may test demand-based activation, but may not introduce it into only one peer arm.

The first cloud-serving experiment uses one evolving mission. This exercises the same durable lifecycle with a one-job sequence; later studies can deliver multiple dependent or independent jobs without changing the architecture.

### Job

A job contains a mission, public materials, declared tool capabilities, submission schema and public feedback policy. Jobs are nested observations, not independently randomized experimental units. A campaign definition decides whether scoring occurs per job, over the campaign as a whole or both.

## Application services

### `ExperimentRunner`

Loads and validates the frozen study and block manifests, mechanically resolves each assigned execution position into an isolated campaign instance, invokes the campaign controller in the registered order, closes submissions and requests evaluation. It never redraws or edits an assignment after execution begins and contains no runtime-specific or task-specific behavior.

### `CampaignController`

Owns the durable organisation lifecycle. It starts the allowed session topology through `HarnessRuntime`, delivers jobs, preserves state between them, enforces campaign deadlines, coordinates snapshots and stops every live session at closure.

It does not decide how agents delegate, communicate, merge work or solve jobs.

### `SubmissionRegistry`

Accepts immutable candidate artifacts, applies identical per-job or per-campaign limits and executes a predeclared neutral selector. Before admission it asks `ArtifactService` to prove that the authenticated submitting actor owns an admitted ordinary `ArtifactRef`; a publication identifier, quarantined object or another actor's reference is rejected. It also atomically reserves the candidate slot and the public evaluator's worst-case GPU time from that actor's allocation. It never combines candidates. Each campaign definition supplies its own default outcome and public ordering semantics; the registry contains no serving-specific or generative-task-specific preference.

An optimization campaign may auto-register its frozen reference artifact as the default and rank candidates by greatest valid public improvement. A generative campaign may instead define a normalized public criterion and a failure-floor outcome when no eligible candidate exists. The same campaign-specific default and selector apply to every condition, so `peer_isolated` is not disadvantaged by lacking a human or agent merger.

### `ArtifactService`

Provides the only general-purpose agent-facing path for moving bytes between workspaces and immutable artifact storage; compute, evaluation and quarantine extraction use separately authorized internal paths. From an authenticated session it can snapshot declared workspace paths into an actor-owned `ArtifactRef`, or materialize an artifact the actor owns or is authorized to read back into that actor's workspace. Paths are canonicalized, bounded and checked against symlink escape.

It also coordinates storage, `PublicationRegistry` and collaboration without coupling their adapters. When an actor publishes an owned artifact, the service idempotently prepares a durable mapping from an opaque `PublicationId` to campaign, owner, artifact and permitted audience, writes the collaboration entry under the same request key, then binds the mapping to that entry. Only bound publications resolve; failed writes leave no usable identifier and recovery cannot create a duplicate entry. A reader asks the service to materialize a publication. The service authenticates the session, resolves the durable record, checks campaign and audience, and then performs a campaign-scoped trusted-service read from storage. That read path is inaccessible to agents and is accepted only with an authorization receipt bound to the artifact and purpose. Agents never receive a transferable storage capability, and a raw artifact reference or copied identifier never bypasses server-side authorization. `QuarantinedArtifact` is a separate, unusable type and cannot be snapshotted, published, submitted or staged for compute; only an approved isolated extractor may admit derived output as an ordinary artifact.

## Infrastructure ports

### `HarnessRuntime`

Creates and resumes primary sessions, enables or denies native handoffs, delivers missions according to the frozen activation policy, streams observational events, snapshots session trees and stops work. The OpenCode adapter uses OpenCode's stock general-purpose subagent behavior in `native_multiagent`; the experiment layer adds no supervisor workflow. Observational collection uses the out-of-process SDK event stream wherever it is sufficient. Any in-process instrumentation plugin is separately pinned and must not register transform hooks, request/tool mutation hooks, tools or commands; this is verified from its allowed API surface and the effective runtime configuration, rather than assumed from OpenCode's plugin boundary. Peer-tool injection belongs to a distinct pinned integration profile that is identical in the two peer arms.

The port exposes a capability manifest so a future Codex, Pi or other adapter can be qualified without pretending that all runtimes have identical semantics.

### `CollaborationBackend`

Provides publish, reply, list, fetch, lexical search, notifications and opaque artifact-publication identifiers. Its authorization scope is configured as:

- `none` for `solo` and `native_multiagent`;
- `actor_private` for `peer_isolated`;
- `organisation_shared` for `peer_collab`.

The two peer modes expose the same tool schema and persistence behavior. A raw artifact reference remains private. Only `ArtifactService` can create or resolve a `PublicationId`, and it authorizes every read against authenticated campaign membership and the stored audience. Actor-private scopes cannot create an organisation-visible publication. V0 has no recommendation, semantic matching, automatic deduplication, task allocation, reputation or privileged-action routing.

### `PublicationRegistry`

Persists the authorization state behind opaque artifact-publication identifiers independently of both collaboration entries and artifact bytes. Records contain campaign, owner, ordinary artifact, audience, bound collaboration entry, lifecycle status and audit metadata. Preparation is not authorization: an identifier resolves only after it is bound to the successfully written entry. Resolution, export and reset are campaign-scoped, and records survive campaign snapshot/resume. The registry is server-only and never exposes a storage reference or read capability to an agent.

### `ComputeBackend`

Executes content-addressed jobs on the declared resource and returns immutable results plus measured usage. The first concrete adapter targets a fixed cloud GPU; local fake and replay adapters support tests. It never receives the experimental condition.

Agents reach it only through the compute capability broker, which owns allowlists, fixed per-actor quotas, immutable input/output staging, exclusive device leases, timeouts and credential isolation. Agent experiments and public evaluator measurements run only in the submitting actor's fixed-duration slots under one frozen `DeterministicActorSchedule`, matched by actor ordinal across the peer conditions. GPU seconds for both count against that actor and the organisation envelope. Slots are non-transferable: unused time remains idle, work cannot borrow another actor's slot, and terminal status or results are released only at the slot's scheduled boundary. Admission therefore depends only on the caller's own allocation and schedule, not competing peer demand. Public-evaluation admission reserves its declared worst-case duration before the candidate is accepted. The tool exposes only coarse state for the caller's own job (`accepted`, `complete` or `failed`), never queue contents, another actor's demand or backend timing.

Scored measurements follow a frozen protocol: restore the declared image and device state, run fixed warmups, execute the declared number and ordering of repetitions, and bracket candidate measurements with a reference canary. Hardware, software, clocks and power settings are recorded where observable. A canary excursion beyond the predeclared tolerance invalidates or retries the measurement according to a condition-blind rule.

### `ResearchBackend`

Searches and fetches evidence under a declared research mode: disabled, frozen corpus or controlled live access. Query history, caches, fixed request/byte allowances and evidence visibility are scoped to the authenticated actor unless a result is explicitly published through the collaboration service, preventing accidental cross-actor or cross-condition communication.

Agents reach it only through the research capability broker, which enforces allowed sources, request quotas, response-size limits and content recording. Controlled live access permits only approved HTTP(S) fetches. The broker validates every DNS resolution and redirect target, blocks private, loopback, link-local, reserved and cloud-metadata addresses, defends against DNS rebinding, limits redirects, bytes and decompressed size, and verifies allowed MIME types. Allowed textual content is decoded and sanitized into a non-executable `SanitizedDocument`; binary or active content remains a content-addressed `QuarantinedArtifact` and is unreadable to tools until an explicit campaign policy admits a declared safe renderer or extractor. The sandbox has no alternate raw-network path.

### `StorageBackend`

Stores append-only events, durable snapshots and content-addressed artifacts. A local adapter may use JSONL, SQLite and the filesystem; later deployments may use remote stores. Storage is infrastructure, not part of a campaign pack.

The backend supports campaign-scoped export and reset. It does not interpret traces, decide validity or enforce budgets; its authorization layer enforces campaign and owner visibility. Agent-facing ingress and materialization go through `ArtifactService`. An internal service read is available only to a pinned trusted service with a purpose- and artifact-bound authorization receipt. `ArtifactService` uses it after authorizing a publication; `SubmissionRegistry` and brokers must instead prove authenticated ownership. Storage never treats a publication identifier as a read grant.

### `Evaluator`

Validates and scores immutable submissions. Public evaluation may return bounded feedback during a job, but it runs through the compute broker in the submitting actor's reserved slots and allocation. Hidden evaluation is callable only after submission closure, uses a separate evaluator-owned measurement schedule/account, and is reported separately from agent treatment spend. The first adapter evaluates model-serving artifacts on a held-out workload.

Evaluation implementations receive campaign/variant data and resource specifications but not the experimental condition or collaboration trace.

The serving evaluator composes a measurement protocol with a separate scoring
profile. The former owns resets, timing and evidence capture; the latter owns
SLOs, eligibility, normalization and aggregation. Both are transitively pinned
campaign data, so changing a score never requires changing Modal, vLLM or the
core evaluator port. Scoring is based on observed API outcomes and remains
blind to the candidate's internal architecture.

### `BudgetGateway`

Terminates all model-provider credentials for a campaign. It conservatively reserves cost before each request, settles against provider usage and a versioned price table, and rejects work that would exceed the applicable limit. In peer conditions, admission must satisfy both the actor's fixed suballocation and the organisation envelope. Peer suballocations partition the envelope and are not transferable, so another actor's spending cannot cause a request to be admitted or rejected. Direct network access from harness sandboxes to model-provider endpoints is denied.

The manifest freezes whether provider caching is disabled, actor-run-scoped or provider-managed and observed. Gateway-controlled caches use separate `(campaign, actor)` namespaces in both peer conditions; confirmatory runs do not deliberately prewarm a condition or share those caches across actors or campaign runs. A confirmatory peer comparison requires effective actor isolation: use a provider namespace when available, otherwise a frozen non-semantic per-actor cache-isolation prefix, or disable caching. Provider-managed observation without effective isolation is permitted for calibration only. The billing policy also freezes the price-catalog digest, rate-schedule version and treatment of provider price tiers or windows. V0 completes a block in one effective tier; a catalog or tier change inside a block invokes the predeclared whole-block rule. For every response the gateway retains requested and returned model identifiers, any revision or system fingerprint, cached-token usage, provider request ID, provider timestamp, effective tier, unit rates and provider receipt. The gateway, not OpenCode's reported cost field, is the enforcement and accounting authority.

The development route uses the simplest valid cache treatment: disabled. The
selected endpoint attests no implicit provider cache, and the transport sends
`X-OpenRouter-Cache: false`. A repeated-request qualification requires zero
cached-token receipts. This same policy applies to every condition; changing it
requires a new profile and study version.

Campaign closure treats the gateway ledger as a validity boundary, not only an
accounting report. Session-token revocation waits for authenticated requests to
finish. The controller then reconciles the ledger against two authorities held
outside that mutable database: the immutable `BudgetPlan` pinned by the
resolved run manifest and a pinned provider-receipt verifier that reconstructs
usage and exact cost from the retained raw stream and metadata bytes. It rejects
the campaign if a stored limit, allocation, rate card, usage digest, charge or
counter differs; if any reservation remains active, was forfeited or overran
its bound; or if required receipts are absent or invalid. Coherently rewriting
ledger rows and terminal audit rows is therefore insufficient to make a
tampered campaign scoreable. HTTP output that reached an agent before a
post-stream defect cannot enter a scoreable campaign result.

An unexpected returned model identity or fingerprint change is handled by a predeclared validity rule. A persistent backend change requires a new `study_version`; a change within a randomized block normally invalidates and reruns the complete block. If the provider exposes no fingerprint, its absence is recorded and closely interleaved blocked runs reduce, but do not eliminate, the resulting threat.

The process sandbox remains a separate adapter from OpenCode. The current
development adapter wraps the complete OpenCode process tree in a pinned macOS
Seatbelt profile, validates that the configured model endpoint is loopback and
retains its profile digest in each harness snapshot. Its kernel policy blocks
nonloopback outbound traffic but permits all ports and services on loopback. It
does not enforce filesystem or process-resource limits. This is a network-only
development control, not the complete sandbox boundary shown above. Scored runs
require a registered adapter that restricts local service access and enforces
the declared filesystem and process-resource boundaries. Other operating
systems require separate adapters and conformance evidence.

## Peer information isolation

`actor_private` is a system-wide information boundary, not merely a message-board filter. In `peer_isolated`, an agent cannot directly observe another actor's candidate artifacts or public feedback, compute or research results, job metadata, caches, collaboration entries, notifications, files or artifact references. Fixed API, GPU, research and submission allocations prevent peers from signalling by consuming another actor's allowance. The deterministic compute schedule is frozen and matched by actor ordinal across paired peer runs; unused slots idle and each actor sees its own terminal state only at its fixed release boundary. Public evaluation follows the same rule. System services may aggregate records only for neutral selection, accounting and post-run analysis after the agent-visible phase closes.

In `peer_collab`, cross-actor information becomes visible only through an explicit publish or reference in the organisation-shared collaboration service. Candidate, evaluator, compute and research services do not silently broadcast results. Thus both peer arms use the same brokers and service behavior; the collaboration authorization scope remains the treatment surface.

## Observation versus enforcement

### Observational evidence

- OpenCode messages, child-session relationships, tool events and cost/token fields.
- Adapter telemetry, latency and resource measurements.
- Collaboration events and stored content.
- The append-only run ledger and human post-run annotations.

These sources support analysis, reconciliation and debugging. Missing or misleading telemetry must not expand an agent's authority or let it exceed a limit.

### Collaboration-use profile

After campaign closure, a read-only `CollaborationProfileBuilder` reports five descriptive dimensions for the two peer conditions beside the task outcome. `peer_isolated` supplies structural-zero controls; `solo` and `native_multiagent` are `not_applicable` unless a separate native-handoff mapping is registered.

- **Reach:** shared publishers and peer retrievers out of `N`, plus directed actor-to-actor links actually realized.
- **Exchange:** unique peer entries returned, replies to peer-authored entries and peer artifacts materialized.
- **Integration:** the selected-artifact owner, distinct peer source actors in its recursively validated ancestry and cross-actor lineage edges.
- **Functions:** outcome-blind human labels for division/help, unsolicited assistance, reuse, challenge/checking, deconfliction and specialization.
- **Overhead:** collaboration tool calls and bytes, attributable context tokens and service latency where reliably measurable.

The manifest freezes definitions, retry deduplication, denominators and the human-label codebook. A shared publisher authors organisation-shared content; a peer retriever receives or materializes another actor's content. A link requires content from one actor to be returned to another; a broadcast that nobody retrieves creates a publisher but no link. Artifact provenance is authorization-validated but remains a lower-bound proxy for semantic influence. If no eligible selected artifact exists, Integration is `not_applicable`. Human reviewers receive a redacted collaboration trace with hidden evaluation events and scores removed, then label each function `observed`, `not_observed` or `unclear` with event references.

The five dimensions are never collapsed into a task score, used to select candidates, or used to subset or adjust the primary treatment estimate. The randomized outcome contrast estimates whether collaboration access helped; the profile describes how the treatment was used and guides later mechanism ablations. The builder is unavailable to agents and evaluators and cannot change campaign state.

### Enforced controls

- The budget gateway controls provider credentials, admission and aggregate API-dollar cutoff.
- Harness sandboxes enforce process, filesystem and network boundaries.
- Compute and research capability brokers authenticate actor/run context, apply policy and enforce quotas.
- Collaboration authorization and the durable publication registry enforce actor-private or organisation-shared visibility server-side.
- Storage namespaces prevent cross-run reads and writes.
- Submission and evaluator services enforce candidate limits and hidden-data separation.

Every enforcement decision also emits observational evidence, but recording a violation is not a substitute for preventing it.

## Condition construction

### `solo`

One primary session receives each job. Native handoffs are denied and no collaboration tool is exposed. The organisation retains its session and workspace across jobs.

### `native_multiagent`

One primary session receives each job and may automatically invoke the runtime's stock general-purpose subagents. No custom roles, supervisor prompt or worker-to-worker channel is added. The primary and resumable child sessions may persist within the campaign according to native runtime semantics. Concurrency and total live identities are capped at `N`.

This condition and `peer_collab` differ in both coordination topology and resource-allocation mechanics. Their contrast evaluates two complete operating approaches; it does not isolate peer topology alone.

### `peer_isolated`

Exactly `N` equivalent primary sessions are activated by the frozen peer policy and receive the same incoming jobs. Each has a private workspace and actor-private service scopes; it cannot discover peers or observe any other actor's entries, artifacts, candidates, feedback, broker jobs, results or caches. Native handoffs are denied.

### `peer_collab`

The same `N` top-level sessions, activation schedule, workspaces, budgets, brokers and tool schema are used, but the collaboration namespace is organisation-shared. No leader, planner, reviewer or finalizer is designated. Roles, lateral delegation, challenge, reuse and work division must arise from agent behavior.

## Isolation, failure and recovery

- Each campaign has fresh harness, collaboration, publication-registry, storage, budget and capability scopes.
- Provider and infrastructure credentials never enter agent context or workspaces.
- Conditions cannot query one another's traces, artifacts, caches or broker state; isolated peers also cannot query those resources across actors.
- Real email, procurement, deployment and other consequential actions are outside V0.
- Human observers are read-only during execution.

Durable campaign snapshots permit operational recovery during development. Confirmatory policy is stricter: an infrastructure-interrupted campaign is either resumed from a predeclared atomic checkpoint for every condition or marked with the predefined failure reason. Ad hoc selective recovery is forbidden.

## Deliberate V0 non-goals

- A multi-tenant dashboard or production deployment.
- Persistence across separate campaigns or studies.
- Semantic organisational search and relevance ranking.
- Automatic matching, deduplication, claims or reputation.
- Human approvals or agent permission escalation.
- Heterogeneous models, roles, tools or authority within a condition.
- Exact deterministic replay of stochastic model output.
- Automated explanation of why collaboration helped or failed.
- Real-world consequential actions.
