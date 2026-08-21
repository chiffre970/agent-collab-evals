# Model budget gateway V0

## Result

The local enforcement proof passed on 2026-08-21. Stock OpenCode completed a
job through the OpenAI-compatible loopback gateway and a deterministic fake
upstream. The gateway admitted the request against a durable organisation and
actor budget, injected the committed model/provider route, streamed the
response unchanged and settled the retained provider usage receipt using a
fixed-point rate card.

This proof used no external API and incurred no model spend. It does not qualify
the live OpenRouter behavior, its billing catalog or the selected DeepInfra
route for a registered study.

## Pinned conformance profile

The conformance-only gateway profile is
`config/gateway_profiles/openrouter-deepinfra-local-conformance-v0.json`. It
transitively binds the existing DeepSeek/OpenRouter/DeepInfra development model
profile, request bounds, cache policy and a clearly labeled synthetic rate
card.

- Gateway profile digest:
  `sha256:2552f52f920f5db4b243d36fba076f7dad87c72ab014766849507a64fc8c6b43`
- Referenced model profile digest:
  `sha256:d09c705b8259f226a69944c59a1792e9dae41b7f026598f12771d71ea3c06762`
- Resolved profile digest:
  `sha256:a37db4d692dcc28f3832b01ab544ec6ce834ae3fcd6f9f24839d102c04a9c85c`
- Synthetic rate-card digest:
  `sha256:f271d51942d25795b21f48a4abdcb9392b1980e6d06d37d7ba822d558635834d`

These rates are test fixtures, not claims about current provider pricing. A live
profile must replace them with a timestamped, source-digested billing catalog
and a separately recorded provider-selection result.

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

Run the loopback proxy and stock-OpenCode proof:

```bash
RUN_MODEL_GATEWAY_INTEGRATION=1 \
  python -W error::ResourceWarning -m unittest -v \
  tests.test_model_gateway tests.test_model_gateway_integration
```

## Remaining boundary

Before any live multi-condition run, freeze a timestamped and source-digested
billing catalog, run one bounded canary through the complete gateway, and pass
the frozen provider-selection workload. The canary must confirm actual
OpenRouter streaming, metadata availability, returned identity, billing and
definitely-unstarted versus ambiguous failure handling. The sandbox must then
deny direct provider egress so the gateway is the only usable model path.

The transport behavior follows OpenRouter's official
[streaming contract](https://openrouter.ai/docs/api/reference/streaming), and
the authoritative post-call receipt is the official
[generation metadata](https://openrouter.ai/docs/api/api-reference/generations/get-request-&-usage-metadata-for-a-generation).
