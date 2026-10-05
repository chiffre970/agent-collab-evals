# Performance-only matched-GPU follow-up

Status: implemented and locally tested; deployment and live completion pending.
The user approved the next run on October 5, 2026.

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
