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

### Provisional latency SLO derivation

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

| Bucket | Worst P95 TTFT | Draft TTFT SLO | Worst P95 TPOT | Draft TPOT SLO | Minimum observed joint attainment |
| --- | ---: | ---: | ---: | ---: | ---: |
| short | 131.460 ms | 150 ms | 39.729 ms | 45 ms | 96.875% |
| medium | 329.309 ms | 400 ms | 50.320 ms | 60 ms | 95.833% |
| long | 1,314.427 ms | 1,450 ms | 77.337 ms | 90 ms | 100% |

The corresponding reference median goodput at the largest offered rate is
3.580009 requests/s for short, 1.306945 requests/s for medium and 0.582939
requests/s for long. Their between-repetition CVs are 0.12%, 0.16% and 0.21%,
respectively.

These values are a draft for evaluator implementation, not a registered-study
mutation. Before registration, the evaluator must replay the rule directly
through pinned vLLM goodput calculation, freeze the exact cross-bucket scalar
and improvement-bound rule, and verify at least one non-reference candidate so
the score is sensitive to a legitimate serving improvement. Registered points
should require zero request failures and at least 90% of requests to meet both
bucket SLOs; failures cannot be hidden by dropping slow requests.

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
4. Do not treat the local ignored store as the final evidence system. A durable
   evaluator-owned evidence adapter and retention policy remain required before
   confirmatory execution.
