# Loopback network sandbox V0

## Result

The development macOS network sandbox passed on 2026-08-22 local time. Stock
OpenCode, native runtime behavior, the peer tool and the budget gateway continue
to work when the entire OpenCode bridge process tree runs through the sandbox.
The same kernel policy permits the loopback experiment gateway and blocks a
server reached through the host's nonloopback interface.

This is development evidence for the current macOS execution environment. It
is not a complete process sandbox and is not a portable claim about a future
Linux or container deployment.

## Enforcement boundary

`DarwinSandboxExec` implements the independent `ProcessSandbox` port. Its
committed profile is
`config/sandbox_profiles/darwin-loopback-network-v0.json`.

The adapter:

- accepts only `127.0.0.1`, `::1` or `localhost` model endpoints;
- wraps the complete OpenCode bridge and descendant process tree with
  `/usr/bin/sandbox-exec`;
- permits every loopback port and service and denies nonloopback outbound
  access;
- receives the existing minimal runtime environment with no provider, Modal,
  GitHub, Hugging Face, storage or cloud credential; and
- records the sandbox profile ID and digest in runtime capabilities and harness
  snapshots.

Snapshot schema `opencode-harness-snapshot/v3` requires the same sandbox digest
on resume. An absent sandbox adapter, a direct OpenRouter endpoint or a changed
sandbox profile fails before an agent session starts.

The profile does not restrict the process to the gateway's loopback port. It
also uses `allow default`, so it does not enforce filesystem paths, child-process
limits, CPU, memory or other process resources. The profile is accurately scoped
as a nonloopback network-egress control only.

## Conformance

Run the profile and kernel-policy tests on macOS with:

```bash
RUN_SANDBOX_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest -v tests.test_sandbox
```

Run the complete sandboxed runtime integration matrix with:

```bash
RUN_OPENCODE_INTEGRATION=1 \
RUN_PEER_TOOL_INTEGRATION=1 \
RUN_MODEL_GATEWAY_INTEGRATION=1 \
RUN_SANDBOX_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest discover -s tests -v
```

## Remaining boundary

Before a scored study, replace or layer this development profile with a pinned
boundary that exposes only the intended local gateway and broker endpoints and
enforces declared filesystem and process-resource limits. Register that
component in the complete platform manifest. If execution moves to Linux or a
container service, implement and qualify the corresponding adapter. Logging an
attempted direct provider request without blocking it is not sufficient.
