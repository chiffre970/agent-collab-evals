# Architecture

## Decision and scope

V0 is a modular experiment system for testing agent collaboration. It is not an early implementation of the full governed-work-network product.

OpenCode is the first coding-agent runtime adapter. It supplies the agent loop, context management, tools and native multi-agent handoffs, but it is not a dependency of the core experiment domain. V0 defaults to the DeepSeek direct API for agent inference, while the model and provider remain independently selected manifest fields rather than core-domain dependencies.

V0 pins and instruments stock OpenCode rather than forking it, and implements the smallest collaboration service needed for the experiment directly behind `CollaborationBackend`. Hugging Face Agent Collabs remains a possible future backend adapter and replication target. The rationale and fork threshold are recorded in [ADR 0001](decisions/0001-agent-runtime-and-collaboration-substrate.md).

The initial study has four conditions:

| Condition | Session topology | Collaboration visibility |
| --- | --- | --- |
| `solo` | One primary session | None |
| `native_multiagent` | One primary plus at most `N - 1` native child sessions | None |
| `peer_isolated` | Exactly `N` equivalent primary sessions | Actor-private only |
| `peer_collab` | Exactly `N` equivalent primary sessions | Organisation-shared |

The two peer conditions use the same fleet topology, collaboration tool schema, candidate policy and aggregate limits. The collaboration backend scopes entries to the originating actor in `peer_isolated` and to the organisation in `peer_collab`. This makes `peer_collab - peer_isolated` the communication estimand. Comparing `peer_collab` with `native_multiagent` tests peer collaboration against conventional hierarchy.

## Design principles

1. **Keep the domain independent of OpenCode.** Core orchestration depends on a `HarnessRuntime` port; OpenCode is the pinned V0 adapter.
2. **Make campaigns durable.** An organisation's identities, sessions, private workspaces and allowed shared state persist across incoming jobs until its campaign closes.
3. **Change the smallest possible treatment surface.** The peer arms use an identical, frozen activation schedule and differ only in collaboration visibility and the minimal instructions that accurately describe it.
4. **Treat the organisation as the resource unit.** All primary, peer, subagent, compaction and retry calls draw from one hard API-dollar account.
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
     |
     +-- HarnessRuntime --------- OpenCode adapter
     +-- CollaborationBackend --- private/shared namespace adapter
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

A study freezes the conditions, adapter versions, model and provider-cache profile, campaign definition, peer activation policy, budgets, randomization, measurement protocol and analysis plan. Replicates within a study differ only by predeclared variant, seed and condition assignment.

### Campaign

A campaign is the lifetime of one condition-assigned organisation. At campaign start, the controller creates:

- stable agent identities;
- persistent harness sessions and session trees;
- one private workspace per top-level agent;
- the condition-scoped collaboration namespace;
- budget and capability accounts;
- campaign-level event and artifact storage.

The controller may then deliver one or more jobs. Sessions and workspaces remain live or resumable between jobs. Campaign state closes only after the job sequence, aggregate deadline or resource envelope ends.

For the two peer conditions, V0 uses one predeclared activation policy. The initial policy eagerly creates exactly `N` top-level sessions, delivers the same job to every session, and applies the same start barrier, concurrency limit, wake/resume rules and deadline. The policy is condition-blind and cannot inspect collaboration content. A later study may test demand-based activation, but may not introduce it into only one peer arm.

The first cloud-serving experiment uses one evolving mission. This exercises the same durable lifecycle with a one-job sequence; later studies can deliver multiple dependent or independent jobs without changing the architecture.

### Job

A job contains a mission, public materials, declared capability grants, submission schema and public feedback policy. Jobs are nested observations, not independently randomized experimental units. A campaign definition decides whether scoring occurs per job, over the campaign as a whole or both.

## Application services

### `ExperimentRunner`

Loads and validates the study manifest, creates isolated campaign instances, randomizes condition order, invokes the campaign controller, closes submissions and requests evaluation. It contains no runtime-specific or task-specific behavior.

### `CampaignController`

Owns the durable organisation lifecycle. It starts the allowed session topology through `HarnessRuntime`, delivers jobs, preserves state between them, enforces campaign deadlines, coordinates snapshots and stops every live session at closure.

It does not decide how agents delegate, communicate, merge work or solve jobs.

### `SubmissionRegistry`

Accepts immutable candidate artifacts, applies identical per-job or per-campaign limits and executes a predeclared neutral selector. It never combines candidates. Each campaign definition supplies its own default outcome and public ordering semantics; the registry contains no serving-specific or generative-task-specific preference.

An optimization campaign may auto-register its frozen reference artifact as the default and rank candidates by greatest valid public improvement. A generative campaign may instead define a normalized public criterion and a failure-floor outcome when no eligible candidate exists. The same campaign-specific default and selector apply to every condition, so `peer_isolated` is not disadvantaged by lacking a human or agent merger.

## Infrastructure ports

### `HarnessRuntime`

Creates and resumes primary sessions, enables or denies native handoffs, delivers missions according to the frozen activation policy, streams observational events, snapshots session trees and stops work. The OpenCode adapter uses OpenCode's stock general-purpose subagent behavior in `native_multiagent`; the experiment layer adds no supervisor workflow.

The port exposes a capability manifest so a future Codex, Pi or other adapter can be qualified without pretending that all runtimes have identical semantics.

### `CollaborationBackend`

Provides publish, reply, list, fetch, lexical search, notifications and immutable artifact references. Its authorization scope is configured as:

- `none` for `solo` and `native_multiagent`;
- `actor_private` for `peer_isolated`;
- `organisation_shared` for `peer_collab`.

The two peer modes expose the same tool schema and persistence behavior. V0 has no recommendation, semantic matching, automatic deduplication, task allocation, reputation or privileged-action routing.

### `ComputeBackend`

Executes content-addressed jobs on the declared resource and returns immutable results plus measured usage. The first concrete adapter targets a fixed cloud GPU; local fake and replay adapters support tests. It never receives the experimental condition.

Agents reach it only through the compute capability broker, which owns allowlists, quotas, exclusive device leases, timeouts and credential isolation. Agent experiments and public or hidden evaluator measurements cannot overlap on the same GPU. The broker exposes only the requesting actor's job state; queue contents, other actors' results and timing-sensitive cache state are not a communication channel.

Scored measurements follow a frozen protocol: restore the declared image and device state, run fixed warmups, execute the declared number and ordering of repetitions, and bracket candidate measurements with a reference canary. Hardware, software, clocks and power settings are recorded where observable. A canary excursion beyond the predeclared tolerance invalidates or retries the measurement according to a condition-blind rule.

### `ResearchBackend`

Searches and fetches evidence under a declared research mode: disabled, frozen corpus or controlled live access. Query history, caches and evidence visibility are scoped to the authenticated actor unless an artifact is explicitly published through the collaboration service, preventing accidental cross-actor or cross-condition communication.

Agents reach it only through the research capability broker, which enforces allowed sources, request quotas, response-size limits and content recording. Controlled live access permits only approved HTTP(S) fetches. The broker validates every DNS resolution and redirect target, blocks private, loopback, link-local, reserved and cloud-metadata addresses, defends against DNS rebinding, limits redirects, bytes and decompressed size, verifies allowed MIME types by headers and content sniffing, and places downloads in a non-executable content-addressed quarantine. The sandbox has no alternate raw-network path.

### `StorageBackend`

Stores append-only events, durable snapshots and content-addressed artifacts. A local adapter may use JSONL, SQLite and the filesystem; later deployments may use remote stores. Storage is infrastructure, not part of a campaign pack.

The backend supports campaign-scoped export and reset. It does not interpret traces, decide validity or enforce budgets; its authorization layer does enforce the declared actor and organisation visibility scopes.

### `Evaluator`

Validates and scores immutable submissions. Public evaluation may return bounded feedback during a job; hidden evaluation is callable only after submission closure. The first adapter evaluates model-serving artifacts on a held-out workload.

Evaluation implementations receive campaign/variant data and resource specifications but not the experimental condition or collaboration trace.

### `BudgetGateway`

Terminates all model-provider credentials for a campaign. It conservatively reserves cost before each request, settles against provider usage and a versioned price table, and rejects work that would exceed the organisation-level cap. Direct network access from harness sandboxes to model-provider endpoints is denied.

The manifest freezes whether provider caching is disabled, actor-run-scoped or provider-managed and observed. Gateway-controlled caches use separate `(campaign, actor)` namespaces in both peer conditions; confirmatory runs do not deliberately prewarm a condition or share those caches across actors or campaign runs. A confirmatory peer comparison requires effective actor isolation: use a provider namespace when available, otherwise a frozen non-semantic per-actor cache-isolation prefix, or disable caching. Provider-managed observation without effective isolation is permitted for calibration only. For every response the gateway retains requested and returned model identifiers, any revision or system fingerprint, cached-token usage, provider request ID and provider receipt. The gateway, not OpenCode's reported cost field, is the enforcement and accounting authority.

An unexpected returned model identity or fingerprint change is handled by a predeclared validity rule. A persistent backend change requires a new `study_version`; a change within a randomized block normally invalidates and reruns the complete block. If the provider exposes no fingerprint, its absence is recorded and closely interleaved blocked runs reduce, but do not eliminate, the resulting threat.

## Peer information isolation

`actor_private` is a system-wide information boundary, not merely a message-board filter. In `peer_isolated`, an agent cannot observe another actor's candidate artifacts or public feedback, compute or research results, queue position or job metadata, caches, collaboration entries, notifications, files or artifact references. System services may aggregate these records only for neutral selection, accounting and post-run analysis after the agent-visible phase closes.

In `peer_collab`, cross-actor information becomes visible only through an explicit publish or reference in the organisation-shared collaboration service. Candidate, evaluator, compute and research services do not silently broadcast results. Thus both peer arms use the same brokers and service behavior; the collaboration authorization scope remains the treatment surface.

## Observation versus enforcement

### Observational evidence

- OpenCode messages, child-session relationships, tool events and cost/token fields.
- Adapter telemetry, latency and resource measurements.
- Collaboration events and stored content.
- The append-only run ledger and human post-run annotations.

These sources support analysis, reconciliation and debugging. Missing or misleading telemetry must not expand an agent's authority or let it exceed a limit.

### Enforced controls

- The budget gateway controls provider credentials, admission and aggregate API-dollar cutoff.
- Harness sandboxes enforce process, filesystem and network boundaries.
- Compute and research capability brokers authenticate actor/run context, apply policy and enforce quotas.
- Collaboration authorization enforces actor-private or organisation-shared visibility server-side.
- Storage namespaces prevent cross-run reads and writes.
- Submission and evaluator services enforce candidate limits and hidden-data separation.

Every enforcement decision also emits observational evidence, but recording a violation is not a substitute for preventing it.

## Condition construction

### `solo`

One primary session receives each job. Native handoffs are denied and no collaboration tool is exposed. The organisation retains its session and workspace across jobs.

### `native_multiagent`

One primary session receives each job and may automatically invoke the runtime's stock general-purpose subagents. No custom roles, supervisor prompt or worker-to-worker channel is added. The primary and resumable child sessions may persist within the campaign according to native runtime semantics. Concurrency and total live identities are capped at `N`.

### `peer_isolated`

Exactly `N` equivalent primary sessions are activated by the frozen peer policy and receive the same incoming jobs. Each has a private workspace and actor-private service scopes; it cannot discover peers or observe any other actor's entries, artifacts, candidates, feedback, broker jobs, results or caches. Native handoffs are denied.

### `peer_collab`

The same `N` top-level sessions, activation schedule, workspaces, budgets, brokers and tool schema are used, but the collaboration namespace is organisation-shared. No leader, planner, reviewer or finalizer is designated. Roles, lateral delegation, challenge, reuse and work division must arise from agent behavior.

## Isolation, failure and recovery

- Each campaign has fresh harness, collaboration, storage, budget and capability scopes.
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
