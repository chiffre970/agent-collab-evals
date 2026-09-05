# ADR 0002: Registered agent sandbox

- **Status:** Accepted direction; implementation candidate is not execution-authorized
- **Decision date:** 2026-09-04

## Context

The macOS `sandbox-exec` adapter proves that the stock OpenCode process tree can
reach a loopback model gateway while direct provider traffic is blocked. It
still permits unrelated loopback services and does not enforce filesystem,
memory, CPU, or process-count limits. Those gaps prevent scored execution.

The sandbox must also remain separate from `OpenCodeHarnessRuntime`. Replacing
OpenCode, the collaboration service, or the model provider must not require a
new containment architecture.

## Decision

V0 will target a Docker-compatible, rootless Open Container Initiative (OCI)
runtime for registered agent containment. Each top-level actor receives one
container with:

- no network namespace connectivity;
- dedicated, session-owned Unix sockets for the model and peer-tool brokers;
- small in-container loopback relays for OpenCode's HTTP clients;
- a read-only root and read-only runtime assets;
- separate writable mounts for only that actor's workspace and runtime state;
- a bounded, non-executable temporary filesystem;
- no provider, Modal, source-control, or storage credentials;
- all Linux capabilities dropped and `no-new-privileges` enabled; and
- fixed CPU, memory, process-count, and wall-time limits.

The model gateway remains the authority for session identity and API cost. The
peer-tool gateway remains the authority for collaboration identity and scope.
The sockets and relays do not carry authority beyond each gateway's existing
opaque, session-bound token. The container cannot route directly to a provider
or to another host service.

`SandboxLaunchContext` supplies the canonical workspace, runtime-state root,
runtime-assets root, model endpoint, and optional broker socket. A sandbox
adapter returns a complete `SandboxedProcess`, including the command, working
directory, and minimal host environment. This prevents an adapter from
silently inheriting the caller's process environment or choosing paths outside
the server-derived session boundary.

The committed
`config/enforcement_profiles/oci-opencode-v0-candidate.json` profile is a
fail-closed implementation candidate. `OciSandboxProfile` validates every
required network, filesystem, credential, process, and conformance value.
`OciSandboxExec` refuses an execution-disabled profile and builds an
argument-vector-only container invocation when supplied a fully registered
profile and pinned engine identity.

The OCI command builder forwards stdin with `--interactive` and requires
`--network none`, a read-only root, explicit
bind mounts, a bounded `noexec` temporary filesystem, dropped capabilities,
`no-new-privileges`, a nonroot user, and CPU, memory, process, and timeout
settings. It accepts only the registered OpenCode bridge command and rejects
unregistered environment keys.

The runtime image includes the peer sidecar beside the bridge. The bridge
resolves that sidecar from its own installed location, not a host repository
path. The container PATH includes the pinned local npm executables. These are
startup requirements; command-builder tests alone do not qualify the image.

The registered deployment must provision each actor's workspace and runtime
state for the profile's fixed UID and GID before launch. Those directories are
the only host paths writable by the container. The broker-socket parent remains
host-controlled and read-only inside the container.

## Why not promote the current macOS adapter

Adding path rules to Seatbelt would not provide the required per-session CPU,
memory, and process-count authority. Renaming that adapter would weaken the
registered contract.

## Why not use Modal Sandbox for V0 agents

Modal Sandbox is a credible later adapter. Its documented gVisor isolation,
network allowlists, filesystem API, CPU and memory limits, and timeouts fit much
of the contract. However, adopting it now would move the OpenCode bridge,
gateway, peer sidecar, and durable session transport across a remote boundary.
The OCI adapter preserves the accepted ADR 0001 runtime and gateway design and
keeps Modal focused on evaluator GPU work. For more information, see the
[Modal Sandbox security model](https://modal.com/docs/guide/sandbox-networking)
and [Modal Sandbox resource limits](https://modal.com/docs/guide/sandbox-resources).

## Promotion gates

The candidate must not authorize execution until all of these gates pass:

1. Build and retain a runtime image by immutable digest.
2. Implement and digest the session launcher and Unix-socket relays.
3. Add Unix-socket listeners to the model and peer-tool gateways without
   weakening their HTTP, token, budget, receipt, identity, or visibility checks.
4. Pin and attest the rootless OCI engine and deployment kernel.
5. Run all required positive and adversarial conformance probes in the target
   deployment environment.
6. Bind the implementation profile, image, launcher, engine, evidence, and
   adapter build digests into the registered study composition.

The live probes must prove access to only the model and peer-tool gateways,
unrelated-loopback denial, provider-egress denial, evaluator-private-file
denial, ambient-credential absence, filesystem write restrictions, and
effective resource limits.

The second and third gates now pass locally. Their source files and digests are
bound by the candidate profile. Each gateway creates one short, dedicated Unix
socket for each opaque token, binds the listener to that token ID, and removes
the socket during revocation. A valid token issued for another socket receives
`403`. The session launcher exposes only the registered in-container model and
peer-tool loopback endpoints and relays bytes to those sockets. Direct and
relayed requests preserve the same budget, receipt, collaboration, visibility,
and identity authorities. OCI image pinning and full container conformance
remain open.

## Consequences

- Registered runs gain a container boundary without coupling containment to
  OpenCode internals.
- The local macOS rehearsal remains useful but remains development-only.
- A Docker-compatible engine becomes a deployment dependency for scored agent
  runs.
- The runtime image build and target-environment conformance probes are the
  next implementation tasks.
- A Modal Sandbox adapter remains possible behind the same runtime and
  sandbox contracts in a later deployment study.
