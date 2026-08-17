# Model-serving V0 calibration ledger

This ledger records engineering calibration decisions. It is not a substitute
for immutable raw evaluator bundles or confirmatory study registration.

## 2026-08-14: stock-reference engineering pilot

One nine-point stock-vLLM repetition ran successfully on a Modal L4 in the
`dev` environment. It was launched from an uncommitted implementation
worktree, so it is an engineering pilot rather than a formal baseline
repetition.

- Modal run: `ap-5IVcY46hH63kygCFOo5zBO`
- Valid points: 9 of 9; 224 requests completed; zero request failures.
- Server process spawn to health: 271,456 ms.
- Warm measured points: 462,121 ms.
- In-container function body: 737,654 ms.
- Client-observed invocation: 746,354 ms.
- GPU: NVIDIA L4, 23,034 MiB, driver 580.95.05, 72 W limit.
- Resolved package-set digest:
  `sha256:4455c0b21d306127bf6f61ddc5319f4898aab49732bc6dbf6a1da2658cd5111b`.
- Local receipt digest:
  `sha256:79f35cb6e052e76205794b17886a91f68b1358e7e8c9f12d25616335fd108e7d`.

The exact raw bundle remains evaluator-private under the ignored local
calibration root. Its nine raw result digests were re-read and verified after
the run.

### Decisions

1. Keep warm steady-state serving as the primary measurement and process
   startup as a separate gate/outcome. The outer Modal call was about 8.7
   seconds longer than the in-container function body and about 284 seconds
   longer than the warmed measurement phase.
2. Promote the observed resolved package digest, GPU memory, driver and power
   limit into the next calibration profile. Future formal baseline runs fail
   closed on drift.
3. Require a clean Git commit and record its identity before another baseline
   allocation is requested.
4. Keep canary latency record-only in calibration. The pre-measurement canary
   included first-use effects (988,377 microseconds), while the post-measurement
   canary took 82,676 microseconds. Both passed the exact functional check, and
   every scored point performed its own fixed warmups.

## 2026-08-14: formal stock-reference calibration

Three sequential repetitions completed from clean, pushed commit
`b1bf8ed9d4d9765f8609e07a3475316986d6a760`. All used measurement-profile
digest
`sha256:5b26369d44fa19450fed0300218181f8ef71e68e503afe0721f3654fc3777bb2`,
campaign digest
`sha256:a214abcdfa4b558f5e96775fc2adb0784a3c6724bd60a79d7734183270583768`
and candidate digest
`sha256:6f1e700b11b3c12cea2ba9c111cc5c81e6493efb83a7deeb74920995ec76e02e`.
No retry was used.

| Repetition | Modal app | Startup | Warm phase | Function body | Client observed | Receipt SHA-256 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 1 | `ap-aDR6QkiPkfER6nSNt2uTjM` | 302.595 s | 451.718 s | 758.106 s | 775.955 s | `286c2beb396394395ba9021e9d785429799f7912571e93d3582fc82eafd9a505` |
| 2 | `ap-Js7Yg2kzLWndGUrp3qOnvV` | 285.440 s | 511.176 s | 802.182 s | 816.183 s | `96d8334b2fbf8b88ae8eb3f28973cdf3572b85afb998f706d9583baec759c18b` |
| 3 | `ap-s9hHZf0bREs2LCqNu12kQT` | 266.372 s | 469.938 s | 740.619 s | 755.778 s | `fe4e16c5db05aaf041740c0783055bf1cb7a48d18516dcbe3b06496ed963db58` |

Across the repetitions, 672 of 672 measured requests completed, all 27 points
and all 27 raw documents validated, and there were no parse, canary, package,
image, GPU-identity or request failures. The raw documents total 3,372,889
bytes and remain in the evaluator-private ignored store. Every receipt records
the same package-set digest, NVIDIA L4 identity, 23,034 MiB memory, driver
580.95.05 and 72 W limit before and after measurement.

The median client-observed lifecycle was 775.955 seconds (range
755.778--816.183; coefficient of variation 3.9%). Startup had a median of
285.440 seconds and CV 6.4%. The complete warm phase had CV 6.4%, mainly from
benchmark-process and warmup overhead. The authoritative per-point request
throughput was much more stable: CV ranged from 0.03% to 0.28% across the nine
points. This supports request-level in-container timing as the score and
lifecycle timing as cost/operations evidence.

The three function bodies used 2,300.907 L4-seconds. At Modal's
[published L4 rate](https://modal.com/pricing) of $0.000222 per second on the
calibration date, the GPU component is approximately $0.511, excluding CPU,
memory, volume and other charges. The workspace billing record, not this
estimate, remains authoritative.

### Latency SLO derivation

The draft policy uses TTFT and TPOT because they independently constrain
prefill and decoding, matching the request-level goodput semantics in
[vLLM 0.21](https://docs.vllm.ai/en/v0.21.0/api/vllm/benchmarks/serve/) and the
[DistServe](https://www.usenix.org/system/files/osdi24-zhong-yinmin.pdf)
framing. It does not also gate E2E: output length is fixed within each bucket,
so an E2E limit would mostly duplicate the same latency and make the joint rule
harder to interpret.

For each bucket and metric, take the maximum P95 over every rate and formal
repetition, multiply it by 1.10, then round upward to 50 ms for TTFT or 5 ms
for TPOT. This rule yields:

| Bucket | Worst P95 TTFT | Calibration TTFT SLO | Worst P95 TPOT | Calibration TPOT SLO | Minimum observed joint attainment |
| --- | ---: | ---: | ---: | ---: | ---: |
| short | 131.460 ms | 150 ms | 39.729 ms | 45 ms | 96.875% |
| medium | 329.309 ms | 400 ms | 50.320 ms | 60 ms | 95.833% |
| long | 1,314.427 ms | 1,450 ms | 77.337 ms | 90 ms | 100% |

The corresponding reference median goodput at the largest offered rate is
3.580009 requests/s for short, 1.306945 requests/s for medium and 0.582939
requests/s for long. Their between-repetition CVs are 0.12%, 0.16% and 0.21%,
respectively.

These values feed a transitively pinned calibration scoring profile. They are
not yet a registered-study policy. Every scored point requires zero request
failures and at least 90% of requests to meet both bucket SLOs; failures cannot
be hidden by dropping slow requests.

### 2026-08-16: goodput replay and scalar freeze

Pinned vLLM 0.21 computes request goodput from its in-memory per-request
latency, TTFT and TPOT. Its saved detailed JSON includes TTFT and inter-token
latencies but omits the exact per-request latency. For the existing reference
evidence, the evaluator therefore reconstructs latency as `TTFT + sum(ITLs)`
only under a 100 microsecond classification guard and a 100 microsecond
aggregate-reconstruction tolerance. The closest reference request was about
1.9 milliseconds from an SLO boundary, so all 672 requests were unambiguous.
New candidate runs pass bucket SLOs to vLLM directly and require its in-memory
`request_goodput`; guarded reconstruction is reference-calibration-only.

The calibration scalar uses the largest offered rate in each workload bucket.
Each bucket's goodput is divided by its three-run reference median, the three
ratios receive equal weight, and three candidate repetition scalars are
aggregated by their median. All nine points remain eligibility gates. The
reported lower measurement bound is the candidate's minimum repetition scalar
relative to the reference's maximum repetition scalar; this deliberately
requires range separation rather than relying on a fragile small-sample normal
approximation.

Offline replay of all 27 raw files reproduced the pinned reference values:

| Repetition | Short goodput | Medium goodput | Long goodput | Scalar (ppm) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 3.581279 | 1.309147 | 0.584524 | 1,001,586 |
| 2 | 3.573482 | 1.306945 | 0.582939 | 999,392 |
| 3 | 3.580009 | 1.304874 | 0.582084 | 998,983 |

The remaining calibration requirement is a legitimate non-reference candidate
run to verify score sensitivity before this formula is considered for a
confirmatory study version.

### Lifecycle decisions

1. Retain the 600-second startup gate and 1,800-second hard repetition limit.
   Use 900 seconds as the expected scheduling duration, but reserve the full
   1,800-second actor allowance so overruns cannot create a shared timing
   channel.
2. Keep canary latency observational. The first canary's CV was 27.5% and
   includes first-use behavior; the post-run canary's CV was 3.0%, but its
   two-token response is not representative of the scored workload. Both
   remain exact functional gates.
3. Use three independent repetitions for candidate evaluation. The observed
   primary-metric noise is small enough that a modest real improvement should
   be distinguishable, while one run would provide no protection against an
   anomalous allocation.
4. Do not treat the local ignored store as the final evidence system. The
   evaluator-owned Modal Volume supplies durable calibration evidence; a frozen
   retention/export policy remains required before confirmatory execution.

## 2026-08-17: sensitivity transport failures and durable evidence correction

The first two attempts at candidate repetition 1 are invalid infrastructure
evidence and are retained locally:

1. Synchronous attempt 1 (`ap-zAf8iANYc30qcwylSg3Yew`, commit `217d137`)
   was canceled when the local Modal client disconnected after 468.929 seconds.
   It produced no remote receipt or raw files.
2. Detached attempt 2 (`ap-dkxrVzYYhSzCPzmDc00PVk`, remote function call
   `fc-01M06HCYY8XFGHX2Z1X55QF9HW`, dispatch commit `278e911`) completed the
   benchmark but failed while packaging its result. The nine raw JSON files
   exceeded Modal's inline result size, causing a blob upload; restricted Modal
   API access correctly rejected that upload with HTTP 401. A separate local
   collector bug also misclassified a nonterminal SDK poll timeout as terminal.

No score from either attempt is admitted. They revealed that raw evaluator
evidence cannot depend on a long-lived local client or the function-result
transport.

The corrected design uses detached `FunctionCall` dispatch, durable call-ID
records and an evaluator-owned Modal v2 Volume. The restricted GPU function
writes raw files plus a digest manifest to the mounted Volume and invokes
filesystem `sync`; it returns only a small receipt. The trusted collector reads
each Volume object by name, verifies every digest, performs normalization and
publishes the normalized receipt once. The local atomic bundle remains a
convenience mirror rather than the sole evidence copy.

A non-GPU restricted-function probe passed in Modal app
`ap-8tDBUJu3ItYsSJoA0v0fn6`: write, v2 Volume sync, trusted readback and digest
verification all succeeded. Candidate calibration restarts under a new
evaluator-issued measurement-series ID so the two immutable invalid attempts
are neither deleted nor overwritten.
