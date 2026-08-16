# Agent-inference provider calibration

Development snapshot: 2026-08-16. This is qualification evidence, not a
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

The public OpenRouter endpoint snapshot listed lower headline prices on some
routes. Baidu was the cheapest clearly established provider in the unrestricted
list at the time of inspection, but a live request failed because no Baidu
endpoint satisfied the requested zero-retention policy. Decart appeared in the
ZDR list at a slightly lower price than DeepInfra, but used an FP4 route and had
lower recent availability. DigitalOcean's input price was similar but its
output price was higher. DeepInfra was the lowest-cost eligible FP8 route in the
inspected set and reported roughly 99.84% trailing-day uptime.

The successful DeepInfra canary attested:

- requested stream model: `deepseek/deepseek-v4-flash-0731`;
- metadata model: `deepseek/deepseek-v4-flash-20260731`;
- serving provider: DeepInfra;
- zero-retention request accepted with fallbacks disabled;
- provider latency: 482 ms;
- generation time: 1,068 ms;
- usage: 21 prompt, 92 completion and 66 reasoning tokens;
- cost: $0.00001824.

Sources: OpenRouter's
[model endpoint API](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-flash-0731/endpoints),
[ZDR endpoint API](https://openrouter.ai/api/v1/endpoints/zdr),
[provider-routing documentation](https://openrouter.ai/docs/guides/routing/provider-selection)
and [ZDR documentation](https://openrouter.ai/docs/guides/features/zdr).

## Registered-study requirement

Prices, uptime and latency are mutable. Before registration, repeat the
qualification with a frozen candidate snapshot, representative request mix,
observation window, thresholds and deterministic tie-break. Store the snapshot,
receipts and selected profile digests in `ProviderSelectionRecord`. Once the
study begins, do not use OpenRouter's dynamic price/latency routing and do not
switch providers within or between conditions in a randomized block.
