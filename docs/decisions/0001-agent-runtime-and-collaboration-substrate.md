# ADR 0001: Agent runtime and collaboration substrate

- **Status:** Accepted for a timeboxed V0 implementation spike
- **Decision date:** 2026-08-12

## Context

The first study needs a capable coding-agent loop, native hierarchical handoffs, durable sessions, complete-enough instrumentation and a collaboration treatment whose visibility and audit semantics we can control exactly. Owning a general agent runtime would divert effort from task environments, evaluation and causal comparison. Adopting a feature-rich collaboration product as the experimental core could make the treatment harder to specify and reproduce.

## Decision

V0 will pin a stock OpenCode release and integrate it through the generic `HarnessRuntime` port. An external controller uses its SDK, a pinned plugin captures available session and tool events, and all model traffic passes through the experiment-owned budget gateway. OpenCode telemetry is observational; the gateway, capability brokers, authorization services and sandbox remain the enforcement boundaries.

We will not fork OpenCode initially. The first part of the spike must prove through the stock SDK and a pinned plugin that the adapter can create and resume durable sessions, expose native handoffs, inject the peer tool, capture the required events and route model traffic through the provider gateway. If a required control cannot be implemented at that boundary, work stops for an explicit runtime decision rather than growing an implicit OpenCode fork.

Subject to that proof, V0 will implement a minimal collaboration backend directly behind the generic `CollaborationBackend` port. Its contract is limited to authenticated actor-private or organisation-shared publish, reply, list, fetch, lexical search, notifications, durable audit events and explicit artifact publication. Artifact authorization is handled server-side: an agent supplies an opaque reference to an artifact it owns, the service validates and records the publication, and authorized readers access it through the published entry. Agents never construct, carry or reason about authorization grants. The service must not assign roles, plan work, merge outputs, recommend collaborators, allocate resources or optimize coordination.

The spike is capped at five focused engineering days: no more than two days for the OpenCode SDK/plugin proof and the remaining time for an end-to-end fake campaign plus the minimal collaboration contract. Exit requires durable resume, matched peer activation at a configured `N` (initially four), private/shared authorization tests, explicit artifact publication, pagination/notification cursors and a complete audit export. These checks may use simple local storage and fake compute; production hardening is not part of the spike.

If the minimal custom backend does not satisfy those exit checks within the timebox, we stop extending it and instead pin and adapt Hugging Face Agent Collabs behind `CollaborationBackend`. We will modify or fork that substrate only as needed to implement the same treatment contract, keeping campaign, evaluator, storage and harness interfaces unchanged. HF Agent Collabs also remains an external replication target if the minimal backend succeeds.

## Consequences

- Engineering effort concentrates on the experimental boundary and outcome evaluation rather than another agent loop.
- OpenCode, the collaboration substrate, model provider and task family can be replaced independently.
- The initial collaboration treatment is deliberately smaller than the long-term product vision.
- V0 tests peer information visibility under fixed allocations. Dynamic pooling, specialist routing and other organisational mechanisms require follow-up studies.
- We accept the maintenance cost of a thin service only if the timeboxed spike demonstrates that it remains thin.
- HF Agent Collabs is the predetermined fallback substrate, not the source of truth for campaign scoring.
