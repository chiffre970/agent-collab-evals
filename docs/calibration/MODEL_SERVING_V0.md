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
