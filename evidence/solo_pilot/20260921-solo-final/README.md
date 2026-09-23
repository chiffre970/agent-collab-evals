# First agent-produced candidate: incomplete solo pilot

Run `solo-final-0921` used controller commit `90746ce`. It aborted during public
evaluation after 1,514.177 seconds. The retained original `audit.json` remains
unmodified and marks the campaign unscoreable. This is not a completed experiment
or evidence for the collaboration thesis.

## Recovered public result

The agent made three model calls and submitted `vllm-chunked-b256-t8192`.
It set `max_num_seqs=256` and `max_num_batched_tokens=8192`; the pinned target,
GPU, engine, and other candidate settings remained unchanged.

- Same-run stock reference: **1,001,223 ppm**.
- Candidate: **998,650 ppm**, approximately **0.26% below** that reference.
- All nine public benchmark points passed validation and eligibility checks.
- Each score represents one repetition. The small difference is not evidence
  of a reliable regression or improvement.
- Hidden quality, correctness, and performance evaluation did not run. Public
  benchmark eligibility does not establish preserved output quality.

The scores use the pinned scoring profile's baseline denominators. The
percentage comparison above divides the two scores from this run; it does not
replace their original denominators.

## Failure and recovery

The local collect-only Modal CLI subprocess exceeded its 360-second deadline.
The controller treated that local timeout as an evaluation failure, although
the remote candidate execution completed successfully. The precise cause of
the collection delay is not established.

Read-only recovery retrieved the existing call output and staging-volume bytes.
No new GPU call was dispatched. The recovery verified the pointer, receipt and
raw-data digests, candidate identity, pinned environment, and GPU metadata,
then parsed and rescored all nine public points. Retained evidence includes:

- `candidate.json`: the submitted configuration.
- `public-reference/` and `reference-result.json`: original reference evidence.
- `remote-call-status.json`: the recovered candidate call pointer.
- `recovered-candidate/`: staging manifest, remote receipt, raw benchmark output,
  and a diagnostic score. This is not a successful campaign-close receipt.

Reference call: `fc-01M31TQP7T3S6FTDZVTV8NTGGP`.
Candidate call: `fc-01M31VH123C2XKQENDFRJ8FGC6`.

The local correction treats a collect-only subprocess timeout as nonterminal,
checks for committed evidence, and otherwise keeps polling the same execution
within the evaluator's overall deadline. Dispatch behavior is unchanged.
Tests cover the durable backend and all three collection transports without
paid calls. Live validation remains outstanding.

## Accounting and cleanup

The model ledger recorded $0.00135162 and passed reconciliation. The original
audit does not establish final Modal billing or a complete run cost. No further
allowances were released during recovery, and no replacement run was launched.
The authorized one-attempt approval is consumed; the cumulative caps remain
$14 Modal and $3 OpenRouter.

Cleanup recorded an `ExceptionGroup` without its underlying causes. Later checks
found no Modal apps or actor containers; the VM was confirmed stopped on
September 22. Those checks do not explain the cleanup exception or convert the
aborted campaign into a completed run.
