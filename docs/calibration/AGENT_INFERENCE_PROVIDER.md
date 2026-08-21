# Agent-inference provider calibration

Development snapshot: 2026-08-21. This is qualification evidence, not a
registered provider choice.

## Decision

Keep DeepInfra through OpenRouter as the default development route for
`deepseek/deepseek-v4-flash-0731`. The route is explicit in
`config/model_profiles/deepseek-v4-flash-openrouter-deepinfra-development.json`;
fallbacks are disabled and the profile is digest-recorded by the preflight.

The choice follows a simple order:

1. require exact returned-model and provider attestation, the needed reasoning
   and tool parameters, no fallback, data-collection denial and zero data
   retention;
2. require a reputable route with acceptable recent availability and a live
   latency canary;
3. among eligible routes, prefer the lowest projected cost for the expected
   coding-agent input/output mix.

This is intentionally not “the cheapest catalog row regardless of behavior.”
Provider implementation and quantization can affect agent capability, so a
provider change is a separate experimental factor and later replication axis.

## Evidence

The executable development policy predeclares the reputable FP8 routes
DeepInfra, Novita and CoreWeave. Exact compressed responses from the OpenRouter
model-endpoint and authenticated ZDR APIs are retained under
`evidence/provider_qualification/sources/` with raw and compressed-file digests.
A deterministic extractor derives the normalized candidate snapshot from those
bytes and rejects any mismatch. The policy requires
the model's tool and reasoning parameters, at least 163,840 context tokens,
32,768 completion tokens, no implicit provider caching and at least 98.5%
trailing-day uptime. For a representative mix of 100,000 uncached input and
10,000 output tokens, deterministic exact-decimal ordering selects DeepInfra at
a projected $0.0098. The policy and candidate snapshot are committed under
`config/provider_qualification/`.

The public OpenRouter endpoint snapshot listed lower headline prices on some
routes. Baidu was the cheapest clearly established provider in the unrestricted
list at the time of inspection, but a live request failed because no Baidu
endpoint satisfied the requested zero-retention policy. Decart appeared in the
ZDR list at a slightly lower price than DeepInfra, but used an FP4 route and had
lower recent availability. DigitalOcean's input price was similar but its
output price was higher. DeepInfra was the lowest-cost eligible FP8 route in the
current candidate set and reported approximately 98.85% trailing-day uptime at
the frozen observation.

ZDR membership does not imply cache isolation. OpenRouter documents that ZDR
endpoints can still use implicit prompt caching. The selector therefore checks
the endpoint catalog's independent `supports_implicit_caching` field, requires
it to be false, and the gateway also sends an explicit response-cache denial.

The successful DeepInfra canary attested:

- requested stream model: `deepseek/deepseek-v4-flash-0731`;
- metadata model: `deepseek/deepseek-v4-flash-20260731`;
- serving provider: DeepInfra;
- zero-retention request accepted with fallbacks disabled;
- provider latency: 482 ms;
- generation time: 1,068 ms;
- usage: 21 prompt, 92 completion and 66 reasoning tokens;
- cost: $0.00001824.

The later evidence-complete route qualification sent two byte-identical text
requests and one forced tool-call request through the actual budget gateway.
All three returned the exact provider and model identities, retained raw stream
and generation receipts, reported zero cached input tokens and passed close-time
budget reconciliation. End-to-end probe times were 11.6 to 12.0 seconds, and
the retained qualification's total provider charge was $0.000052. The exact
qualification record plus all six stream and metadata receipts are retained
under `evidence/provider_qualification/`; loading the selected route resolves
and digest-checks every file. This remains development evidence.

Sources: OpenRouter's
[model endpoint API](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-flash-0731/endpoints),
[ZDR endpoint API](https://openrouter.ai/api/v1/endpoints/zdr),
[provider-routing documentation](https://openrouter.ai/docs/guides/routing/provider-selection)
and [ZDR documentation](https://openrouter.ai/docs/guides/features/zdr).

## Registered-study requirement

Prices, uptime and latency are mutable. Before registration, create registered
copies of the raw source bundle, derived snapshot, policy and selection record
after the declared observation window. Mirror the source and receipt evidence
to evaluator-owned durable storage. Once the study begins, do not use
OpenRouter's dynamic routing and do not switch providers within or between
conditions in a randomized block.
