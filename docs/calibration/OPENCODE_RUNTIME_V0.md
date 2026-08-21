# OpenCode runtime V0 spike

## Result

ADR 0001 passed on 2026-08-21. Stock OpenCode is suitable for the V0
`HarnessRuntime` adapter without a fork or an in-process instrumentation
plugin. The separately pinned peer-tool path and the minimal collaboration,
publication and storage services also passed their exit checks.

The test used a deterministic local OpenAI-compatible gateway. It consumed no
model API or GPU resources.

## Pinned software

| Component | Version | npm integrity |
|---|---:|---|
| `opencode-ai` | 1.18.19 | `sha512-+lD0mLSZdnS3hzDFmrhH1epH6pXY2KRCGTNE+/i6sXmontPd+CEO8ruWOhE/myutLLhvaiPa249ZAaK6p5nlww==` |
| `@opencode-ai/sdk` | 1.18.19 | `sha512-AnszRg7cJ3PA6/06mkqdTJDKn9NJuV26AJMWbKEgRsznbJvrhf3PT8UhQQOhyKQYCygx9ZOxyKMIPVOQdMSS1A==` |

The exact packages, platform binaries and transitive dependencies are retained
in `package-lock.json`, whose digest is
`sha256:4b3f55bbf1299d8b1f73a06342188a4b664a5c27ea2640ce86df59187f152c20`.
The committed development runtime profile resolves to
`sha256:b9e8158ee07451f5f44197f145e5fae30566e48918344618f6191656f1ca7098`.
That digest also binds the separate DeepSeek/OpenRouter/DeepInfra development
profile and the complete npm lockfile. It will change when either committed
profile or the pinned dependency graph changes.

The peer-tool profile pins `@modelcontextprotocol/sdk` 1.30.0, its exact five
operations and the local MCP server implementation. It resolves to
`sha256:4d8f554ce5eb84abda4d81ae466be11e956b3b7835e4b43af79cee1c371769b9`.

## Stock SDK conformance

The strengthened probe passed all of the following:

- Six model requests went only to the local gateway and requested only its
  declared deterministic model.
- A primary session retained its identifier and both messages across a complete
  stock-server restart.
- The model invoked OpenCode's stock `task` tool. OpenCode created one child
  session, the child completed through the same gateway and the parent resumed.
- The out-of-process SDK event stream captured the child session. It emitted
  175 events across 13 event types in the retained run, including session,
  message, part, status and diff events.
- Effective configuration, model, tool, permission and agent digests were
  unchanged while observation was active.
- The complete outbound model request, including effective system prompt and
  tool schemas, was byte-canonically identical in matched observed and
  unobserved sessions.
- In the solo profile, both effective configuration and the actual model request
  omitted access to the `task` tool.

The retained run used ephemeral loopback ports, so endpoint-containing
configuration digests are intentionally run-specific. The invariant is equality
before and after observation within a run. The detailed ignored report was
written to `tmp/opencode-conformance/report.json`. Run-specific request and
prompt digests are printed in that report.

## Harness adapter conformance

The Python adapter uses a small persistent JSON-lines bridge to the pinned SDK.
It starts one OpenCode server, workspace and XDG state namespace per top-level
actor. Native `task` calls are enabled only for `native_multiagent`; the
subagent cannot recursively invoke `task`. Provider/model/runtime choices come
from the committed profile. The adapter requires an external
`GatewayTokenIssuer`, obtains a distinct opaque revocable token for each
top-level session and never stores its value in a snapshot. The bridge receives
an explicit minimal environment with isolated HOME, TMPDIR and XDG roots; it
does not inherit ambient provider, Modal, GitHub or Hugging Face credentials.

The real-runtime integration test delivered one job, atomically serialized the
campaign snapshot, released the first bridge, created a fresh runtime adapter,
resumed the same OpenCode session, delivered a second job and closed the
campaign. The fake gateway received exactly two requests for the pinned model.
The snapshot contained redacted surface digests and no token value. Its event
checkpoint had contiguous monotonic cursors, a terminal reconciled session tree,
message counts and message digests, no stream error and no buffer-loss marker.
The adapter rejects a snapshot if any reconciled session or message identifier
is absent from accumulated events. Float-valued event fields are retained as
their exact JSON decimal strings so canonical experiment persistence remains
unambiguous. The first session token was revoked at suspend, resume used a new
token and closure revoked it.

Bridge timeouts are terminal: the process is killed and marked unusable so a
late response cannot corrupt the next request's sequence. Failed multi-session
resume removes every provisional session mapping while closing bridges and
revoking the newly issued tokens.

## Matched peer-tool conformance

The peer profile exposes `publish`, `list_recent`, `get_thread`, `search` and
`notifications` through one local MCP server. OpenCode presents these as the
same `peer_*` tools in `peer_isolated` and `peer_collab`; native `task` remains
disabled in both. The gateway derives the campaign and actor from a
session-bound sidecar credential rather than tool arguments. The credential is
not stored in snapshots or returned to the model, and is revoked with its
runtime transport.

The real-runtime test used four actors per peer arm. It verified identical
effective configuration, model, tool, permission and agent digests after an MCP
readiness barrier. OpenCode-generated actor workspace and runtime paths are the
only values normalized before comparison. Every private actor saw only its own
publication, while shared actors observed peer publications through the same
tool calls. A shared campaign survived suspension, runtime replacement and a
second delivered job. The collaboration audit contained no cross-actor reads
in the private arm and did contain them in the shared arm.

## Commands

```bash
npm run spike:opencode
RUN_OPENCODE_INTEGRATION=1 python -W error::ResourceWarning \
  -m unittest -v tests.test_opencode_harness_integration
RUN_PEER_TOOL_INTEGRATION=1 python -W error::ResourceWarning \
  -m unittest -v tests.test_peer_tool_integration
```

Both commands require loopback port binding. Neither requires `.env`, an API
account or Modal access; the integration test supplies an in-memory fake token
issuer.

## Remaining boundary

The adapter proves that model traffic can be routed to an arbitrary compatible
endpoint, but the endpoint used in a real study must be the external budget
gateway. OpenCode telemetry does not enforce dollars, provider selection,
privacy policy or model identity. Direct OpenRouter study traffic is therefore
not enabled by this result.

The runtime and collaboration-substrate decision is complete. The next gate is
the external provider budget gateway and the remaining enforcement-service
vertical slice. Filesystem-safe workspace snapshot/materialization, submission,
compute and evaluator services remain separate implementation work before a
registered multi-condition run.
