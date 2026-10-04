# Retained quality environment rejection and recovery plan

`solo-devbound-1003` aborted at hidden evaluation after about 92.4 minutes.
Unlike the preceding aborts, collection succeeded: the rejected quality bundle
contains all 64 raw documents, a complete receipt, and a diagnostic score.
Its GPU driver was `610.57.04`; the measurement contract requires `580.95.05`.
The original run is aborted, non-scoreable, and has no final hidden outcome.

The immediate adapter defect was interpreting `valid=false` with a populated
quality score as malformed evidence. Environment rejection can coexist with a
diagnostic score. The fix verifies the retained seal, records a failed execution
with the validation reasons and uncapped measured duration, and excludes that
score from the quality series. The resolver profile is now V2. New quality and
correctness functions check trusted GPU pins before starting the model or
running cases. These changes do not relax the measurement contract.

## No-spend replay

The dedicated VM retained the original checkout at
`34ca54219c112869cc82f9197c431a626b9f2d29`. A diagnostic loaded the development
resolver modules in memory, reconstructed the original profiles, and read the
original ledgers without opening writable adapters. It verified the rejected
bundle, excluded its score, and recovered a 504-second measured duration.
The source audit, manifests, ledgers, and raw results were unchanged.
No new scored dispatch, GPU invocation, model call, or spend authority occurred.

The source evidence is private:

- Audit: `/home/rmh.guest/ace-runs/solo-devbound-1003/audit.json`, digest
  `sha256:be6dd8d3e8f223baa9f316f414b059943c80bf09ad1772b4359eb47e83b92e32`.
- Run configuration digest:
  `sha256:5ec5bca6aeb45a2573e3ed01c102aeb2ff0c55785db3f72d05a5bd5a7803e7af`.
- Rejected call: `fc-01M412JEVP8JYJRPFSF35EJRWY`.
- Rejected local bundle receipt digest:
  `sha256:18cd2158f094391d238d2dde48716636e4f9e29c2a9e6e1bfe5ea44e7064582b`.
- Diagnostic root:
  `/home/rmh.guest/agent-collab-evals/.private/solo-devbound-1003/recovery-plan`.
- Conformance digest:
  `sha256:3f38031a3f35e0adcde845fc4743519e628cc05080ada6d3b25c733dcb30742e`.
- Recovery plan digest:
  `sha256:bd7da80d6718bd20b5f60d918c511517a214f2b659289501719f5e9f4a24eb36`.

## Evaluation-only recovery boundary

The executable planner identifies:

- Six verified results to reuse: public reference, public candidate, hidden
  correctness, and three quality executions.
- One environment-rejected quality execution requiring replacement.
- Five never-dispatched executions: two quality and three performance.

The plan retains source result snapshots with their original evidence profiles.
It rejects corrupt evidence, unretained pending calls, consumed authority with
missing dispatch records, and ordinary quality failures. It cannot dispatch or
poll providers, issue spend authority, start an agent, rewrite the original audit,
or make a continuation scoreable.

The separate continuation is implemented. It executes only the outstanding
evaluations into new state, binds replacement build/profile provenance,
reconciles all six requests and consumed authorities, and combines the complete
series using the existing scoring functions. It rechecks the neutral public
selection instead of trusting mutable submission scores. A restart collects
existing calls or reuses completed evidence, without redispatch. Failures and
ambiguous deliveries require an explicit operator decision, not automatic retry.
The result is exploratory and non-scoreable, with mixed-build provenance; it
does not reopen the source campaign or create a new agent session.

The new V11 settlement validator independently reconstructs the original model
charge from its frozen plan and raw provider receipts. Dollar admission reserves
the entire six-job ceiling before issuing any request authority. The operator
authority requires a clean pinned source commit, binds the exact manifest/state
and journal, and expires within 24 hours. It permits no agent/model spending.
The local suite ran 444 tests: 421 passed and 23 live integrations were skipped.

## Spending review

Read-only provider queries confirm terminal results for all seven dispatched
calls. Modal's call metadata binds each call to its billing app. No active dev
app or actor container was found. The current hourly snapshot attributes
$1.20508955 to those seven apps, including CPU, memory, and L4 resources.
The original reconciled model ledger records $0.00429174 across five calls.
This is a current usage snapshot, not a final provider invoice.

The retained evidence is under
`/home/rmh.guest/agent-collab-evals/.private/solo-devbound-1003/settlement`:

- Terminal observations digest:
  `sha256:e39b53e09136947d0097c274b9e8391bacca95e1f02f0121c4d4c172c415ffea`.
- Raw billing snapshot digest:
  `sha256:50babedb39a485385806e05bf4f46d1da12809896eae45bb0e6479b6c256dd25`.
- Settlement preview digest:
  `sha256:3dd43de460b43e627ec9c7594e8e7e0c194f75038810ac8935e92bbce187439b`.

The preview proposes retaining the observed Modal usage plus a $0.10 buffer and
releasing unused allowances from this attempt only. After reviewed settlement,
remaining admission capacity would be $10.28106498 Modal and $2.96824940
OpenRouter. The six-execution continuation's conservative allowance is
$5.59648 Modal, including $1 of overhead, and no additional model allowance.
It fits the existing cumulative $20 Modal / $3.10 OpenRouter ceilings.
Earlier reservations remain unchanged. This preview has not advanced the
journal, issued authority, or started paid work.

## Production offline continuation check

The dedicated VM used an isolated non-secret source snapshot to prepare all six
production Modal requests against the real selected artifact and hidden bundle.
Constructor checks composed the actual SQLite execution and authorization
services without issuing authority. V11 settlement was fully validated, then
all seven dollar admissions (six GPU calls plus overhead) were tested on a
temporary journal copy. The check also confirmed model spending is denied and
an older amendment cannot reopen the journal. The original checkout, run
evidence and shared spend journal were unchanged. No provider call ran.

- Prepared state:
  `/home/rmh.guest/agent-collab-evals/.private/solo-devbound-1003/evaluation-continuation-v2`.
- Continuation manifest digest:
  `sha256:4993c7563cb61924c4a2dbbd3ebd594f187fe4dbc6466ea4b2d81bdd993aff21`.
- Proposed V11 amendment digest:
  `sha256:595bfe718937f230b6365637f6ac169495e0ed15b35371d19e7a96da3e9de351`.
- Offline conformance digest:
  `sha256:72a4037554d437fd9ea5192ef1c2d261452d1b384d93b8ecccfeb07878299581`.
- Approval proposal digest:
  `sha256:645b60653255eefd2120d00a6a747dfc28fddcc1d34770c6fbf9d49a3539985a`.

After reviewed settlement, the shared journal would have $10.28106498 Modal and
$2.96824940 OpenRouter available. Reserving the six-job allowance of $5.59648
Modal would leave $4.68458498 Modal; OpenRouter capacity would be unchanged.
These are admission balances, not invoices. The actual journal has not been
settled or charged for this continuation. Earlier unresolved reserves are held.

Remaining gates, in order:

1. Commit and deploy the exact tested source build. A changed build requires a
   new preparation and digest; do not overwrite the retained manifest.
2. Obtain explicit approval for one six-job evaluation-only continuation under
   the existing $20 Modal / $3.10 OpenRouter cumulative ceilings. Then issue an
   expiring authority bound to the clean commit and exact prepared manifest.
3. Apply the reviewed settlement and reserve the full $5.59648 allowance before
   dispatch. Run three outstanding quality calls and three performance calls.
4. Reconcile all new execution evidence, verify the reused results, and publish
   the separate exploratory outcome. Do not mark the source run successful.

Pinned GPU checks remain enabled. A new environment rejection stops this
continuation; the implementation does not promise Modal will return the old
driver or silently relax that requirement. Performance also retains its
post-measurement environment checks.

`prepare-solo-evaluation-continuation` is the no-spend CLI. The separate
`run-solo-evaluation-continuation` command requires an authorization file and
its independently supplied digest. Neither command requires an OpenRouter key.

## Planner command

Run this command from a development checkout with access to the original pinned
source checkout and private workload. An updated checkout is not a substitute
for the original measurement inputs. The output directory must be separate
from the source run.

```sh
.venv/bin/python -m agent_collab_evals plan-solo-evaluation-recovery \
  --source-root /home/rmh.guest/ace-runs/solo-devbound-1003 \
  --audit-digest sha256:be6dd8d3e8f223baa9f316f414b059943c80bf09ad1772b4359eb47e83b92e32 \
  --config /home/rmh.guest/agent-collab-evals/.private/solo-devbound-1003/configuration.json \
  --source-repository /path/to/original-34ca542-checkout \
  --output-root /path/to/separate-recovery-plan
```

There is deliberately no execution or authorization flag on this command.
