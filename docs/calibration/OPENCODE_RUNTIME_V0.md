# OpenCode runtime V0 spike

## Result

The stock-runtime portion of ADR 0001 passed on 2026-08-21. Stock OpenCode is
suitable for the V0 `HarnessRuntime` adapter without a fork or an in-process
instrumentation plugin. This is a runtime decision, not completion of the full
ADR: the peer-tool and collaboration/publication service checks remain pending.

The test used a deterministic local OpenAI-compatible gateway. It consumed no
model API or GPU resources.

## Pinned software

| Component | Version | npm integrity |
|---|---:|---|
| `opencode-ai` | 1.18.19 | `sha512-+lD0mLSZdnS3hzDFmrhH1epH6pXY2KRCGTNE+/i6sXmontPd+CEO8ruWOhE/myutLLhvaiPa249ZAaK6p5nlww==` |
| `@opencode-ai/sdk` | 1.18.19 | `sha512-AnszRg7cJ3PA6/06mkqdTJDKn9NJuV26AJMWbKEgRsznbJvrhf3PT8UhQQOhyKQYCygx9ZOxyKMIPVOQdMSS1A==` |

The exact packages, platform binaries and transitive dependencies are retained
in `package-lock.json`, whose digest is
`sha256:cc8e2c6b001d3d106d956b2285711a4f3f720d8eeab71fb06b0575d57e06da7d`.
The committed development runtime profile resolves to
`sha256:5790cf3f5fb6c924fee75851b05b810f7da7c391f4abfdee877c4d4315a9f8eb`.
That digest also binds the separate DeepSeek/OpenRouter/DeepInfra development
profile and the complete npm lockfile. It will change when either committed
profile or the pinned dependency graph changes.

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

## Commands

```bash
npm run spike:opencode
RUN_OPENCODE_INTEGRATION=1 python -W error::ResourceWarning \
  -m unittest -v tests.test_opencode_harness_integration
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

ADR 0001 now proceeds to the matched peer-tool and minimal collaboration,
publication, storage and session-identity slice. Its private/shared
authorization, durable publication, cursor, audit export and four-peer checks
must pass before the ADR is complete.
