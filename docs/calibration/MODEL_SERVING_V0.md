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

This paragraph records the 2026-08-17 calibration implementation. The
2026-08-28 security correction below supersedes that implementation for future
runs; it does not rewrite the retained historical evidence.

A non-GPU restricted-function probe passed in Modal app
`ap-8tDBUJu3ItYsSJoA0v0fn6`: write, v2 Volume sync, trusted readback and digest
verification all succeeded. Candidate calibration restarts under a new
evaluator-issued measurement-series ID so the two immutable invalid attempts
are neither deleted nor overwritten.

## 2026-08-28: scored-function security correction

The earlier GPU function mounted the writable evidence Volume and received the
Hugging Face secret while starting candidate-supplied argv. That boundary is
not safe for agent-authored candidates and is retired for future runs.

Candidate schema v0alpha2 removes executable argv. It accepts only typed,
bounded vLLM settings from a fixed allowlist, and evaluator code constructs the
complete command. Scored GPU functions receive no secret, block external
networking, mount the prepopulated model cache read-only and do not mount the
durable evaluator evidence Volume. The initial correction returned a bounded,
compressed result bundle to a separate trusted persistence function. The
2026-08-29 staging correction below supersedes that large-result transport
without weakening the other controls.

This correction changes the campaign and evaluator-script digests. Historical
calibration results remain development evidence for the earlier build and are
not silently relabeled. A new bounded live conformance run must verify the
read-only cache, blocked-network and split-persistence path before further
candidate calibration.

The dedicated `--security-conformance` path performs this proof with one short
served-generation request plus the pre- and post-canaries. It uses the same
hardened scored function and separate persistence function without running the
full nine-point benchmark.

## 2026-08-29: hardened Modal boundary conformance

The bounded conformance passed in Modal app
`ap-0GqUrJYPvaFQqRZDlHyt68`. The scored L4 function had external networking
blocked, mounted the model cache read-only, received no secret and had no
evaluator-evidence mount. After the candidate process exited, a separate
trusted function persisted the bounded bundle to the evaluator-owned Volume;
the collector resolved and verified every digest.

The first two restricted attempts, `ap-0Md9qCxGojrCuZe9gDCeql` and
`ap-Q4gV3KBt6SYJoxY5vXZZ59`, failed closed before model startup. The current
Hugging Face offline resolver required three repository-documentation files
that vLLM's successful authenticated cache warm-up did not fetch. The fix does
not relax the boundary: evaluator code now supplies vLLM the fixed local cache
path for the pinned revision instead of asking the offline resolver to
re-enumerate the repository. Candidate input cannot change that path. An
authenticated smoke in `ap-R58aaEAiRZuJ1SWbXIfU84` had already proved that
the exact cached runtime files start stock vLLM 0.21.0 on an L4.

The passing receipt binds evaluator script
`sha256:939e867fbfa29be4642a7d5f68b656dac64a32d2b1de675b7ec48694204e671b`,
remote receipt
`sha256:a33e61a1472729bee9dc53db580c71b3280bc06fb064f7b4d651a7684b93cbb3`
and raw response
`sha256:52006566eeca2b670ad8cedd714eb6c3ee12dae7448c0defac6f4c3916abdb3c`.
The evidence root is
`security-conformance/e2ffcbb6f73641edb2832fa18dbb8614`. Server startup
took 258.352 seconds, the one-case evaluation took 104 milliseconds and total
function-body time was 263.223 seconds. This is development conformance
evidence, not a scored result or registered-study qualification.

## 2026-08-29: durable dispatch and large-evidence correction

The first full dispatch through the durable compute backend used run
`modal-development-reference-v2-20260829` and function call
`fc-01M16CY51E42D19PC499WQE15H`. The transport recorded the external call but
the enclosing ephemeral Modal app was not detached, so Modal stopped the child
before useful work. The terminal `RemoteError` is retained and reconciled as an
invalid infrastructure run. Because no authoritative runtime receipt exists,
accounting conservatively charges the full 1,800-second reservation. No score
was admitted.

Transport profile v0alpha3 adds Modal's `--detach` lifecycle and binds that
choice into the profile digest. It also admits a tightly validated terminal
infrastructure-failure document without requiring success-only durable scoring
evidence. Run `modal-development-reference-v3-20260829`, function call
`fc-01M16D6BX3E2KQMA4ZJJ3563GP`, then remained live through model startup and
the complete benchmark. At result serialization, Modal attempted a large-object
blob upload from the restricted function and correctly returned HTTP 401. App
`ap-qu5Df0UzYGBWY8zegDMcfz` retains the exact traceback. This run is also
terminal, invalid and unscored.

Large scored results no longer use the function-result channel. The evaluator
dynamically mounts only a unique subpath of
`agent-collab-evals-evaluator-staging-v2`. The candidate process is stopped
before evaluator code writes and syncs the evidence directory. The restricted
function returns a small pointer; a trusted collector resolves and verifies
every staged digest, then invokes the separate trusted durable-persistence
function. Other staging subpaths and the durable evidence Volume are never
visible to the scored function.

A CPU-only 4 MiB conformance passed in app
`ap-A2jD68SKFucWANRN7hi3y5`. Durable evidence root
`preflight-staging/85f43096b76e4257bd704f8fe526420c` binds remote receipt
`sha256:e76e143a6398ce421b5a6cb6a0616178fd158ef8b9e1f341ad7700ea255b08b1`
and raw artifact
`sha256:a3ebc8b7ababa2914d3336cde5434f5cbe404f8e865cb6a7ce5cd384c6f4ab99`.
An earlier probe, `ap-AJ4zhGy6uvUMDkCG4UJ5CW`, exposed a transient Volume
visibility delay after sync; collection now applies a bounded 30-second
visibility barrier. These are development transport results, not model scores.

The replacement run `modal-development-reference-v4-20260829` completed from
commit `f63afa6692c9041a71c7d7e27b58fe9c0824392d` under function call
`fc-01M16MV5FBW2PQEBWY13GY9H0F`. All nine benchmark points completed without
parse, environment, or scoring failures. Startup took 283.434 seconds,
measured points took 460.331 seconds, and the authoritative function-body
duration was 748.887 seconds, recorded as 749 used seconds.

The eligible reference score is 996,024 ppm. Bucket ratios were 994,748 ppm
for short, 995,509 ppm for medium, and 997,816 ppm for long; selected goodput
was 3.561208, 1.301075, and 0.581666 requests per second, respectively. These
are development calibration measurements, not a treatment comparison.

Durable evidence root
`model-serving/8c419db915d474db3aab39fc365869bffd94d344ba0cb7cc0e0f00c36452f68a/repetition-0001-attempt-01`
binds remote receipt
`sha256:6bf7583a2085897419bc293dab754befa36aa71cd2b2ebc1daaf3111ff48580d`
and normalized result
`sha256:e90d4b2fc51d914e25d5502bba464b350ac745522fb897d717948ad613b1a50e`.
Close-time reconciliation reconstructed the sole request from frozen manifest
`sha256:76f819d5011a29188fc6641b27a7188fb19f150b75fccfb45964a749dc3c95b5`
and re-resolved dispatch plus evidence digest
`sha256:2e3a41d7b3bef1aa518e1620c56ed70d67a0879f8c6321fe52839e2e21640acf`.
The development durable-dispatch gate is complete.

Commit `67a1403` added explicit performance-profile binding and therefore
invalidated the earlier script-specific conformance proof. The replacement
bounded check passed in Modal app `ap-qe4vCriDPxPlv7EjoAZ5w3` on 2026-08-30.
It bound evaluator script
`sha256:fd37240034069275c93610fba8f759839a1a29a82a87d864d12fc53d071d77d0`,
evidence root `security-conformance/aa36dd88dde343718f13b5049deac055`,
remote receipt
`sha256:00f6cdc77127db66f2e79bc9559cb78606ac26de49b7ee034e9dfa88c029a29a`
and raw result
`sha256:01ea3d2b4a7419aaf9fc98478d47801435ae6d16a854b78c28743bb9b3832f3c`.
External networking was blocked, the model cache was read-only, the scored
function had no secret or durable evidence mount, and isolated staging plus
separate trusted persistence both passed. Startup took 293.424 seconds, the
single request took 104 milliseconds and function-body time was 298.461
seconds. This remains development conformance, not scored study evidence.

## 2026-08-30: durable hidden-quality compute conformance

One explicitly authorized reference repetition exercised the new hidden-quality
path from private-bundle resolution through durable spend authorization, Modal
dispatch, retained evidence normalization, compute accounting, and close-time
reconciliation. The scored function ran from commit `fb688a4` under function
call `fc-01M196A529QH097YJ52KG17WDR`. It completed all 64 private cases with no
validation errors: 55 passed, producing a valid quality score of 859,375 ppm.
Startup took 275.404 seconds, case evaluation took 292.930 seconds, and the
authoritative function-body duration was 573.661 seconds, recorded as 574 used
seconds.

The first trusted collection encountered a transient empty response while
reading the staged `manifest.json`. The earlier visibility barrier retried only
`FileNotFoundError`, so it rejected the already-completed function call without
creating terminal local evidence. Commit `46056f8` extends the barrier to retry
missing, empty, and digest-incomplete reads. Recovery app
`ap-nFMKEqArpo9iCTPoq5N5JD` reused the original function call and dispatched no
second scored GPU job.

Durable evidence root
`model-serving-quality/25528e275c352a9d1353cb4f029cb513e015ce3937536c5c8c56242b4fa47411/repetition-0001-attempt-01`
binds 64 raw response digests, remote receipt
`sha256:08b9785a19b623f912954cf63a1c194fa4a00ca1c7da146042688f56fdc5e6ee`
and normalized result
`sha256:3385e80653215599d24a07177c2a2935a34cccc9c7e11de9a5d008baee3bc3c8`.
The durable compute envelope has evidence digest
`sha256:d8c3e46a45ef6e3400ce79b62fa7244c8df3a651c6cde712820fa6749bd3ed2f`.
Close-time reconciliation reconstructed the sole request from frozen manifest
`sha256:797506a8ab61c913494b7378ba19bb952bf97f85f02ffaa1cd84591fba32fdb0`.
This is development conformance evidence, not a treatment result or registered
study qualification.

## 2026-08-17: valid non-reference sensitivity series

The replacement series
`candidate-stream-interval-10-sensitivity-v2` completed three sequential,
first-attempt repetitions. The candidate differed from stock vLLM only by
setting the documented stream interval to 10. Every repetition used the same
pinned model, image, package set and Modal L4 identity; all 27 points were
eligible, all 672 requests completed, and the collector reported no parse or
environment errors.

| Repetition | Modal app / function call | Startup | Warm phase | Function body | Scalar | Local receipt SHA-256 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 1 | `ap-26lQtgVhr8wZ0J3xOHQs3n` / `fc-01M06JNYZGCXCWBXB1NKTZ389F` | 319.599 s | 491.964 s | 820.017 s | 1,002,037 ppm | `561a2647f6e6289269f460250d65bf66a555b0026ff8f2667952a71a0b4d9fdf` |
| 2 | `ap-axRjzEjX4p0DvQ4YoboVlR` / `fc-01M06KH55D0KWPA5J82V4AKCEG` | 218.257 s | 429.977 s | 652.002 s | 1,000,787 ppm | `c5bdd68486e30cf85793f5416cff5e095288dc612a82061aa36ed9e9ee97bdfa` |
| 3 | `ap-WOOHGgDNKpi0W9xgS3hiJl` / `fc-01M06M80C46PXD3W5RVCFCTGCR` | 212.255 s | 419.483 s | 635.431 s | 1,001,872 ppm | `36326ca2dc760fa175ebcc045ef693b669fedd78555778be00fcbe571bccb074` |

The median candidate score is 1,001,872 ppm. All three candidate repetitions
are above the reference median of 1,000,000 ppm, and the candidate median is
286 ppm above the largest reference repetition. The deliberately conservative
candidate-minimum/reference-maximum measurement bound is nevertheless -798
ppm. Therefore this series establishes that a legitimate non-reference launch
is scored through direct in-memory vLLM goodput and that the score moved in the
expected direction; it does **not** establish that this small stream-interval
change reliably improves performance beyond measurement variation.

For every repetition, the restricted GPU function committed nine raw files,
its remote receipt and a digest manifest to
`agent-collab-evals-evaluator-evidence-v2`; the trusted collector verified
those bytes and published an immutable normalized receipt in the same remote
directory. The ignored local bundles are mirrors, not the evidence authority.
