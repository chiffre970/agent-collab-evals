# Performance-only matched-GPU follow-up

Status: first follow-up stopped during polling; collector correction awaiting
live qualification. No paired outcome exists.
The user approved the next run on October 5, 2026.

Implementation commit: `a9c2416bb59db294f55ee881659c3cf278d3932c`.
Local validation: 455 tests ran, 432 passed, and 23 live integrations were
skipped, with resource warnings treated as errors. The deployed no-spend check
resolved all historical settlement and evidence references, preserved the 90
existing admissions, reused nine completed results, and issued no authority.
All four previous continuation calls were provider-confirmed successful and
terminal before launch; no dev apps or actor containers remained active.

The detached runner started at `2026-10-05T02:16:51Z`. Its first paired execution
was `fc-01M44XKBN4W4GEGP0MYJ6J4QP0`. At `2026-10-05T02:18:07Z`, the collector
mistook a built-in `TimeoutError` from an unfinished `FunctionCall.get()` poll
for a fatal bridge error. The runner requested cancellation; the provider
subsequently reported the call terminated and app `ap-XNXRa8BQRj3KWomiPCAHlf`
stopped. The last two executions never started. The VM state root is
`/home/rmh.guest/agent-collab-evals/.private/solo-devbound-1003/paired-performance-1005`.
The VM remains pinned to the stopped implementation until another deployment.
All evidence and reservations remain unchanged.

The correction makes collection-only poll timeouts nonterminal, but does not
swallow Modal's genuine function timeout or output-expiration exceptions.
Regressions exercise the installed SDK's exact polling path and the production
bridge with the real durable backend: three unfinished polls, one dispatch,
then sealed completion. These tests use simulated provider responses, not a
new paid call. Live qualification and a new one-use authorization remain gates
for any replacement run.

Post-fix validation: 458 tests ran, 435 passed, and 23 live integrations were
skipped, with resource warnings treated as errors. The current provider billing
snapshot totals $0.01799946 for the cancelled app. This is not a final invoice
or a refund authorization; all admission reserves remain held.

Retained document digests:

- Manifest: `sha256:bc8c3cbb3ef78c1342d9a4fcbef6c1d38e48d5e7dfc713a00b2f80586e6f6f40`.
- Offline conformance: `sha256:46111c3343ee3e1cb86a2bd5be8a9e70fefa42178b9fa09a4b8cedb98c11cf44`.
- Amendment: `sha256:e48f0223f2ae8c61199f4bcf43d40d6ed0508b0be67a4d8f2ef3b83815facd24`.
- Launch: `sha256:a773e022b8ca1b5479d6ec170fa64f44f10054095b510db56c7271704f40efe9`.
- Original audit, unchanged: `sha256:be6dd8d3e8f223baa9f316f414b059943c80bf09ad1772b4359eb47e83b92e32`.

`solo-evalonly-1004` stopped during its first performance collection at
`2026-10-04T05:19:29Z`. All three outstanding quality executions completed.
The rejected performance receipt retains nine raw outputs and 664,657 ms of
function-body time. It reports driver `610.57.04`, while the original profile
requires `580.95.05`. The adapter also failed to translate this sealed rejection
into a failed compute receipt. No final outcome exists, and the last two jobs
were never dispatched. Neither the original audit nor the stopped ledger is
rewritten.

Modal controls the host driver; it is not a container image setting. See
[Modal's CUDA documentation](https://modal.com/docs/guide/cuda). Repeatedly
retrying the same exact-driver profile is not a reliable qualification strategy.

The separate exploratory follow-up consists of three same-GPU pairs. Each pair
runs stock reference and selected candidate in one restricted single-use L4
container, with fresh processes, disjoint engine caches and result directories.
Order is reference/candidate, candidate/reference, reference/candidate.
The driver must be either `580.95.05` or `610.57.04` and remain unchanged across
both roles, along with physical GPU identity, capacity and power setting.
Unknown drivers fail before model startup. Software, model, hidden performance
workload, warmups, latency limits and candidate bytes remain pinned.

The existing goodput parser and latency qualification apply to both roles.
Each bucket's denominator is the newly measured same-GPU stock reference at
the existing selected request rate. The result reports the median and minimum
observed candidate/reference ratio over three pairs. Neither statistic is a
confidence bound or the original registered score. Ineligible measurements
remain measured outcomes; failed, missing, altered or overrun evidence cannot
close successfully.

The nine completed public, correctness and quality inputs retain their original
profile and evidence digests. Quality is re-evaluated under the policy frozen in
the stopped continuation. It is not rerun on a different driver. Therefore, a
new-driver pair can establish diagnostic performance, but cannot independently
qualify preserved quality on that driver. The outcome labels this explicitly and
is always non-scoreable. The registered measurement/scoring files are unchanged.

The full new allowance is $4.413952 Modal, including three worst-case 3,000-second
GPU jobs, startup/cleanup margins and $1 shared overhead. No OpenRouter calls or
allowances are permitted. All historical reservations, including uncertain
dispatches, remain held. No automatic refund, cap increase, extra attempt or
agent rerun is allowed. Current published rates agree with the pinned cost
profile: [Modal pricing](https://modal.com/pricing).

Preparation and execution are separate:

```sh
.venv/bin/python -m agent_collab_evals.solo_performance_followup prepare \
  --root /absolute/new-state \
  --configuration /absolute/configuration.json \
  --retained-inputs /absolute/retained-inputs.json \
  --run-id solo-paired-performance-1005
```

The trusted operator first captures the completed inputs from the original
frozen adapters. Preparation issues no authority and makes no provider calls.
Execution requires a clean pinned deployment, a digest-resolved amendment to
the existing shared spend journal, and fresh expiring authority:

```sh
.venv/bin/python -m agent_collab_evals.solo_performance_followup run \
  --root /absolute/new-state \
  --authorization /absolute/authorization.json \
  --authorization-digest sha256:INDEPENDENT_APPROVAL_DIGEST
```
