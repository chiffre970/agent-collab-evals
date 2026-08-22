# Model budget gateway V0

## Result

The local enforcement proof passed on 2026-08-21. Stock OpenCode completed a
job through the OpenAI-compatible loopback gateway and a deterministic fake
upstream. The gateway admitted the request against a durable organisation and
actor budget, injected the committed model/provider route, streamed the
response unchanged and settled the retained provider usage receipt using a
fixed-point rate card.

The dependency-free transport then passed one bounded live development canary
through the same gateway. This qualifies the implementation path against the
current development profile; it does not select or qualify a route for a
registered study.

## Pinned conformance profile

The conformance-only gateway profile is
`config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json`. It
transitively binds the existing DeepSeek/OpenRouter/DeepInfra development model
profile, request bounds, cache policy and a clearly labeled synthetic rate
card.

- Gateway profile digest:
  `sha256:047e21c598281ee28af8c39ef474ed18820a81f819f87ba22499bd5e6fe13cf5`
- Referenced model profile digest:
  `sha256:d09c705b8259f226a69944c59a1792e9dae41b7f026598f12771d71ea3c06762`
- Synthetic billing-catalog digest:
  `sha256:2347c47085d781a539a3a21cb8dd4170be771e2e44b3819dc3a4cf748bf43927`
- Resolved profile digest:
  `sha256:39b5d1f8348aa08d71b14ee3508cce699c7863e699bea3a01c6f98be643f2307`
- Synthetic rate-card digest:
  `sha256:81efcc68d5495f4341cdee1c12c9d0c3896e76148c11439e5845062e71f2cde4`

These rates are test fixtures, not claims about current provider pricing. The
separate development profile references the public catalog observed at
2026-08-21T11:15:09Z and has resolved digest
`sha256:ea60b9121ca3fa539ed447a6f4c94e3d2135c4a593c418057f773c7ab04d35a2`.
Its billing-catalog digest is
`sha256:dcc6352175758385c4e4b7e1ca5c92d2befb2a84bc0a05999638741fc5400c97`.
The catalog records $0.08 per million uncached input tokens, $0.016 per million
cached input tokens and $0.18 per million output tokens. Its exact per-token
source strings are mechanically checked against the integer nanodollar rates.

## Live development canary

The evidence-complete canary completed at 2026-08-21T11:20:38Z with no retry:

- provider: DeepInfra;
- stream model: `deepseek/deepseek-v4-flash-0731`;
- metadata model: `deepseek/deepseek-v4-flash-20260731`;
- usage: 16 prompt, zero cached and 56 completion tokens;
- authoritative billed cost: 11,360 USD nanodollars (`$0.00001136`);
- metadata receipt digest:
  `sha256:486fe369d9db63bbca65d4b21c77f0bd917ecb37ffb3f16241a1ec77036257ae`.

The key-free summary is retained under ignored `tmp/preflight/`. The current
preflight also preserves the raw SSE and generation JSON there as owner-readable
files; registered evidence will instead use evaluator-owned durable storage.
An earlier successful transport check cost `$0.00000812` but preserved only
digests; it is recorded as an invalid evidence-lifecycle attempt rather than
substituted for the corrected run. Total model spend across both checks was
`$0.00001948`.

## Enforcement proved

- USD amounts use integer nanodollars. Token charges use ceiling division over
  integer rates per million tokens, with no binary floating-point accounting.
- Actor allocations partition the organisation limit exactly. Admission holds
  both reservations atomically, and concurrent requests cannot oversubscribe
  either account.
- A request must fit both the actor and organisation remainder before upstream
  execution. A rejection invokes no upstream.
- Missing or invalid usage, incomplete streams, upstream ambiguity and returned
  route drift consume the full conservative reservation and remain audited.
- Exact provider usage, stream bytes, requested and returned model, provider,
  request ID, provider timestamp, fingerprint, cached input, token counts, rate
  tier and unit rates survive SQLite restart and digest verification.
- A dependency-free OpenRouter transport captures `X-Generation-Id`, streams
  the response, then retrieves the correlated generation record with bounded
  retries. The gateway independently verifies the stream model, metadata model,
  provider, generation identifier and native token counts; it retains both raw
  receipts and settles against the exact billed total when present.
- Model and provider-routing fields supplied by a caller are overwritten by the
  committed profile. Caller-supplied actor headers have no effect.
- The opaque model credential is issued pending, activated against the actual
  OpenCode session and revoked on failure, suspend or stop. It is not stored in
  campaign snapshots.
- Revocation rejects new authentication and waits for authenticated in-flight
  requests to reach a durable terminal state. Mandatory close-time
  reconciliation then checks limits and allocations against an immutable
  out-of-ledger budget plan and reconstructs usage and cost from raw provider
  bytes with a separately pinned receipt verifier. It rejects coherent database
  rewrites, active reservations, forfeitures, overruns, missing or invalid
  receipts and ledger-counter inconsistencies, so a post-stream defect cannot
  leave a scoreable campaign.
- OpenCode handles decimal-valued inference parameters through a dedicated
  exact decimal JSON path; experiment money and evidence boundaries continue to
  reject binary floats.

## Commands

Run the nonnetwork account and profile tests:

```bash
python -W error::ResourceWarning -m unittest -v \
  tests.test_sqlite_budget tests.test_model_gateway
```

Run the deterministic OpenRouter transport and metadata-receipt proof:

```bash
python -W error::ResourceWarning -m unittest -v \
  tests.test_openrouter_upstream tests.test_sqlite_budget
```

Validate the development profile without spend, then explicitly run the
budget-bounded canary with:

```bash
npm run check:model-gateway
npm run preflight:model-gateway
```

Run the loopback proxy and stock-OpenCode proof:

```bash
RUN_MODEL_GATEWAY_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest -v \
  tests.test_model_gateway tests.test_model_gateway_integration
```

## Remaining boundary

Development provider selection and condition-matched disabled-cache evidence
now pass. The macOS runtime also uses a kernel-enforced, loopback-wide network
policy, so OpenCode cannot reach the provider directly. Every loopback service
remains reachable, and filesystem and process-resource limits are not enforced.
Before any scored run, promote the selected route, billing and gateway evidence,
register the immutable budget plan and independent receipt-verifier profile,
add the missing sandbox boundaries, and bind them into the complete platform
manifest. A different deployment operating system requires its own sandbox
adapter and equivalent proof. Failure-injection tests continue to provide the
deterministic unstarted and ambiguous-request evidence.

The transport behavior follows OpenRouter's official
[streaming contract](https://openrouter.ai/docs/api/reference/streaming), and
the authoritative post-call receipt is the official
[generation metadata](https://openrouter.ai/docs/api/api-reference/generations/get-request-&-usage-metadata-for-a-generation).
