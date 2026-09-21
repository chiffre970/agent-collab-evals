# September 21 solo retry

Run `solo-retry-0921`, controller commit `c116488efd2bdd3af6abe766320781104c793420`,
aborted during the public reference after 732.794 seconds. No agent, model API
call, candidate optimization, or hidden evaluation ran. This is not a scored
experiment result.

The remote reference returned `evaluator evidence destination already differs`.
Both the previous run and this fresh run derived their global Modal evidence
destination from the same reference measurement ID. Write-once persistence
correctly refused to replace the earlier output. The missing boundary was a
distinct namespace for each run's measurement store.

The fix persists a new store namespace before dispatch and combines it with the
measurement ID for public performance, hidden performance, quality, and
correctness evidence. Collection and restart reuse that namespace. A copied
store retains its identity; fresh runs require fresh stores. Legacy dispatches
without a namespace require their original collector. Previous remote evidence
has not been deleted or overwritten. The fix has not had another live GPU run.

The retained audit confirms terminal execution cleanup. On September 21 at
approximately 10:36 UTC, Modal listed no active apps and the VM listed no actor
containers. The VM was then stopped. A read-only billing report for September
21–22 attributes $0.22239952 to app `ap-J5XP8pVGmTJZ3BCb8YxcyI`; this is a
reported app cost, not complete invoice reconciliation. Function call:
`fc-01M317DXVSZ1XA8C305D3ZCT9T`.

The original journal retains the retry's admissions: $5.06432 Modal and $2.95
OpenRouter net of the explicitly reviewed first model-allowance release.
Remaining admission capacity is $8.93568 Modal and $0.05 OpenRouter. Unused retry
allowances have not been released. The authorization permitted one attempt;
no further attempt has been authorized or launched.
