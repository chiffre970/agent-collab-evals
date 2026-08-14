# ADR 0001: Agent runtime and collaboration substrate

- **Status:** Accepted for a timeboxed V0 implementation spike
- **Decision date:** 2026-08-12

## Context

The first study needs a capable coding-agent loop, native hierarchical handoffs, durable sessions, complete-enough instrumentation and a collaboration treatment whose visibility and audit semantics we can control exactly. Owning a general agent runtime would divert effort from task environments, evaluation and causal comparison. Adopting a feature-rich collaboration product as the experimental core could make the treatment harder to specify and reproduce.

## Decision

V0 will pin a stock OpenCode release and integrate it through the generic `HarnessRuntime` port. An external controller uses its pinned SDK and consumes the public event stream out of process wherever that is sufficient. Because OpenCode's current [V2 plugin API](https://opencode.ai/v2/docs/build/plugins) is beta and can transform agents, models, requests and tools, any in-process instrumentation plugin is separately pinned, restricted to an explicit observational API/hook allowlist and verified not to mutate effective prompts, models, tools, permissions or configuration. Peer-tool injection uses a distinct pinned integration profile, identical in the two peer arms. All model traffic passes through the experiment-owned budget gateway. OpenCode telemetry is observational; the gateway, capability brokers, authorization services and sandbox remain the enforcement boundaries.

We will not fork OpenCode initially. The first part of the spike must prove through the stock SDK plus the separately pinned observational and peer-tool paths that the adapter can create and resume durable sessions, expose native handoffs, inject the peer tool, capture the required events and route model traffic through the provider gateway. It must export effective prompt/model/tool/permission/configuration digests and demonstrate that observational instrumentation leaves them unchanged. If a required control cannot be implemented at that boundary, work stops for an explicit runtime decision rather than growing an implicit OpenCode fork.

Subject to that proof, V0 will implement a minimal collaboration backend directly behind the generic `CollaborationBackend` port. Its contract is limited to authenticated actor-private or organisation-shared publish, reply, list, fetch, lexical search, notifications, durable audit events and explicit artifact publication. Artifact authorization is handled server-side through the independent durable `PublicationRegistry`: an agent supplies a reference to an ordinary artifact it owns, the service creates and binds an opaque publication to the successful entry, and authorized readers materialize it through a session-bound service check. Agents never construct, carry or reason about authorization grants. The service must not assign roles, plan work, merge outputs, recommend collaborators, allocate resources or optimize coordination.

The spike is capped at five focused engineering days: no more than two days for the OpenCode SDK/plugin proof and the remaining time for an end-to-end fake campaign plus the minimal collaboration contract. Exit requires durable session/workspace resume; matched peer activation at a configured `N` (initially four); proof that observational instrumentation leaves effective prompts, models, tools and permissions unchanged; private/shared authorization tests; idempotent artifact publication whose bound registry record survives resume; successful authorized peer materialization through the trusted service-read path; rejection of raw, unbound and wrong-audience references; pagination/notification cursors; and a complete audit export. These checks may use simple local storage and fake compute; production hardening is not part of the spike.

If the minimal custom backend does not satisfy those exit checks within the timebox, we stop extending it and run a bounded conformance assessment of candidate substrates, beginning with [Hugging Face Agent Collabs](https://github.com/huggingface/agent-collabs). There is no automatic switch. A candidate must demonstrate both actor-private and organisation-shared twin modes without changing the client tool, session-bound server-derived identity, opaque server-authorized artifact publication, durable audit/export and pagination/cursors, campaign reset/resume, and a local or fake mode that preserves treatment semantics. HF Agent Collabs' existing organization/scratch/central-bucket layout is evidence that it may be adaptable, not evidence that these requirements already hold.

Adoption requires a follow-up ADR that records the assessed commit, gaps and adapter/fork scope. If a modest adapter or bounded fork passes the same conformance suite, it may be pinned behind `CollaborationBackend` and `PublicationRegistry` while campaign, evaluator, storage and harness interfaces remain unchanged. Otherwise we make an explicit architecture/timebox decision rather than silently weakening the experiment. HF Agent Collabs remains an external replication target if the minimal backend succeeds.

## Consequences

- Engineering effort concentrates on the experimental boundary and outcome evaluation rather than another agent loop.
- OpenCode, the collaboration substrate, model provider and task family can be replaced independently.
- The initial collaboration treatment is deliberately smaller than the long-term product vision.
- V0 tests peer information visibility under fixed allocations. Dynamic pooling, specialist routing and other organisational mechanisms require follow-up studies.
- We accept the maintenance cost of a thin service only if the timeboxed spike demonstrates that it remains thin.
- No third-party substrate is a fallback by name alone; it must pass the treatment and authorization contract before adoption.
