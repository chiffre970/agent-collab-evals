# Architecture

## Decision and scope

V0 is a modular experiment system for testing agent collaboration. It is not an early implementation of the full governed-work-network product.

OpenCode is the first coding-agent runtime adapter. It supplies the agent loop, context management, tools and native multi-agent handoffs, but it is not a dependency of the core experiment domain. The model and provider are selected independently in the study manifest; the initial profile may use DeepSeek without making the system DeepSeek-specific.

The initial study has four conditions:

| Condition | Session topology | Collaboration visibility |
| --- | --- | --- |
| `solo` | One primary session | None |
| `native_multiagent` | One primary plus at most `N - 1` native child sessions | None |
| `peer_isolated` | Up to `N` equivalent primary sessions | Actor-private only |
| `peer_collab` | Up to `N` equivalent primary sessions | Organisation-shared |

The two peer conditions use the same fleet topology, collaboration tool schema, candidate policy and aggregate limits. The collaboration backend scopes entries to the originating actor in `peer_isolated` and to the organisation in `peer_collab`. This makes `peer_collab - peer_isolated` the communication estimand. Comparing `peer_collab` with `native_multiagent` tests peer collaboration against conventional hierarchy.

## Design principles

1. **Keep the domain independent of OpenCode.** Core orchestration depends on a `HarnessRuntime` port; OpenCode is the pinned V0 adapter.
2. **Make campaigns durable.** An organisation's identities, sessions, private workspaces and allowed shared state persist across incoming jobs until its campaign closes.
3. **Change the smallest possible treatment surface.** The peer arms differ only in collaboration visibility and the minimal instructions that accurately describe it.
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

A study freezes the conditions, adapter versions, model profile, campaign definition, budgets, randomization and analysis plan. Replicates within a study differ only by predeclared variant, seed and condition assignment.

### Campaign

A campaign is the lifetime of one condition-assigned organisation. At campaign start, the controller creates:

- stable agent identities;
- persistent harness sessions and session trees;
- one private workspace per top-level agent;
- the condition-scoped collaboration namespace;
- budget and capability accounts;
- campaign-level event and artifact storage.

The controller may then deliver one or more jobs. Sessions and workspaces remain live or resumable between jobs. Campaign state closes only after the job sequence, aggregate deadline or resource envelope ends.

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

Accepts immutable candidate artifacts, applies identical per-job or per-campaign limits and executes a predeclared neutral selector. It never combines candidates. The peer conditions receive the same selector so `peer_isolated` is not disadvantaged by lacking a human or agent merger.

## Infrastructure ports

### `HarnessRuntime`

Creates and resumes primary sessions, enables or denies native handoffs, delivers missions, streams observational events, snapshots session trees and stops work. The OpenCode adapter uses OpenCode's stock general-purpose subagent behavior in `native_multiagent`; the experiment layer adds no supervisor workflow.

The port exposes a capability manifest so a future Codex, Pi or other adapter can be qualified without pretending that all runtimes have identical semantics.

### `CollaborationBackend`

Provides publish, reply, list, fetch, lexical search, notifications and immutable artifact references. Its authorization scope is configured as:

- `none` for `solo` and `native_multiagent`;
- `actor_private` for `peer_isolated`;
- `organisation_shared` for `peer_collab`.

The two peer modes expose the same tool schema and persistence behavior. V0 has no recommendation, semantic matching, automatic deduplication, task allocation, reputation or privileged-action routing.

### `ComputeBackend`

Executes content-addressed jobs on the declared resource and returns immutable results plus measured usage. The first concrete adapter targets a fixed cloud GPU; local fake and replay adapters support tests. It never receives the experimental condition.

Agents reach it only through the compute capability broker, which owns allowlists, quotas, leases, timeouts and credential isolation.

### `ResearchBackend`

Searches and fetches evidence under a declared research mode: disabled, frozen corpus or controlled live access. Query history, caches and evidence visibility are scoped to the organisation or actor as specified by the study, preventing accidental cross-condition communication.

Agents reach it only through the research capability broker, which enforces allowed sources, request quotas, response-size limits and content recording.

### `StorageBackend`

Stores append-only events, durable snapshots and content-addressed artifacts. A local adapter may use JSONL, SQLite and the filesystem; later deployments may use remote stores. Storage is infrastructure, not part of a campaign pack.

The backend supports campaign-scoped export and reset. It does not interpret traces, decide validity or enforce agent permissions.

### `Evaluator`

Validates and scores immutable submissions. Public evaluation may return bounded feedback during a job; hidden evaluation is callable only after submission closure. The first adapter evaluates model-serving artifacts on a held-out workload.

Evaluation implementations receive campaign/variant data and resource specifications but not the experimental condition or collaboration trace.

### `BudgetGateway`

Terminates all model-provider credentials for a campaign. It conservatively reserves cost before each request, settles against provider usage and a versioned price table, and rejects work that would exceed the organisation-level cap. Direct network access from harness sandboxes to model-provider endpoints is denied.

The gateway, not OpenCode's reported cost field, is the enforcement and accounting authority.

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

Up to `N` equivalent primary sessions receive the same incoming jobs. Each has a private workspace and actor-private collaboration namespace; it cannot discover peers or read their entries or artifacts. Native handoffs are denied.

### `peer_collab`

The same top-level sessions, workspaces, budgets and tool schema are used, but the collaboration namespace is organisation-shared. No leader, planner, reviewer or finalizer is designated. Roles, lateral delegation, challenge, reuse and work division must arise from agent behavior.

## Isolation, failure and recovery

- Each campaign has fresh harness, collaboration, storage, budget and capability scopes.
- Provider and infrastructure credentials never enter agent context or workspaces.
- Conditions cannot query one another's traces, artifacts, caches or broker state.
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
