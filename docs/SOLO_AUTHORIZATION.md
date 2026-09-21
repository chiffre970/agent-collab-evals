# Authorize one exploratory solo attempt

The existing `solo-pilot` command accepts an operator-owned approval file and
an independently supplied SHA-256 digest. Without both, live execution remains
closed. A configuration's `execution_authorized` value stays `false`: experiment
settings are not permission to spend.

The gate reuses the existing lifecycle, transports, budget guard, and cleanup.
It permits the pinned Podman `development_conformance` boundary for one
exploratory solo attempt. It does not label that boundary registered or change
the registered-study path. An operator approval is a trusted-controller decision,
not a digital signature or an automated proof of arbitrary readiness evidence.

## Prepare the approval

First, review the deployment, full evaluator prerequisites, billing headroom,
and retained qualifications. Retain those assessments outside actor mounts.
Do not issue an approval merely because the device/cancellation check passed.

Use schema `exploratory-solo-authorization/v1`, with these exact fields:

- `scope`: `one_exploratory_solo_attempt`.
- `run_id`: the single intended run ID.
- `expires_at`: timezone-aware ISO timestamp within the next 24 hours. Keep it
  inside the verified provider billing cycle. Expiry is checked before run
  admission and before each compute authorization.
- `configuration_digest`: the result of
  `solo_authorization.configuration_binding(configuration)`. This binds the
  configuration, transitive campaign/model/runtime/compute inputs, hidden
  workload validation, and controller/runtime source files.
- `spend_plan_digest` and `qualification_admissions_digest`: the existing
  journal's plan digest and `digest_value(snapshot["receipts"])` respectively.
  Both required qualification admissions must already be present. The provider
  admission must match the selected gateway and qualified route.
- `spend_journal` and `state_root`: absolute controller paths. The journal is
  the existing VM journal, not the repository evidence copy. It must be separate
  from run state; the private workload must also remain outside run state.
- `engine_executable` and `engine_identity_digest`: the absolute Podman binary
  and retained engine identity. The gate rechecks the binary, engine version,
  kernel, rootless/systemd/cgroup-v2 configuration, runtime, and ID mappings.
  The live configuration must pin that same identity and the qualified image.
- `provider_selection`: `{ "file": "...", "digest": "sha256:..." }` for the
  retained selection record. Its raw provider receipts are independently checked.
- `readiness_evidence`: references in the same file/digest shape, with exactly
  `deployment`, `evaluator`, `billing`, and `modal_cancellation` keys.
  The cancellation outcome must match its journal admission. The other files
  retain the operator's reviewed assessments; their hashes protect identity,
  not the truth of an unreviewed assertion.

Retain the approval separately from run state and actor mounts. Review the exact
bytes, then supply their digest explicitly. There is no automatic approval-file
generator, no approval in `.env`, and no default approval shipped in the repo.

## Execute

From the trusted VM controller, with credentials injected only into that process:

```sh
.venv/bin/python -m agent_collab_evals solo-pilot \
  --config .private/first-solo/configuration.json \
  --state-root /home/rmh.guest/ace-runs \
  --run-id first-solo \
  --authorization .private/first-solo/authorization.json \
  --authorization-digest sha256:REVIEWED_APPROVAL_DIGEST
```

The gate validates the full remaining allowance before dispatch. The journal
uses a single-attempt reservation key independent of the run ID; a new run ID
cannot restart the attempt. Failures do not refund admissions. The run audit
retains the exact operator approval alongside its configuration and spend plan.

Use a short, durable state root and bind that exact path in the approval. Before
any spend admission or reference dispatch, the lifecycle validates both full
per-session socket paths, including their token directories and filenames. Each
resolved UTF-8 path must be shorter than 100 bytes. A deeply nested repository
path can exceed that limit even when the run ID is short. Invalid paths retain
an aborted preflight audit but consume no new admission allowance. Do not move
or overwrite a previous attempt to reuse its run ID.

The existing public reference phase runs before the model gateway or actor starts.
The lifecycle resolves and retains its receipt and result, and aborts if the
reference is ineligible or reports failures. This is a stop condition within the
single attempt, not another qualification run. Failure consumes its admitted
allowances and invokes the same cleanup path; it does not permit an automatic
retry. Passing the public reference does not replace hidden quality evaluation.

`--check` remains offline and does not authorize or start work. Passing it does
not prove deployment or evaluator readiness. Registered and multi-condition
experiments remain outside this command's exploratory authorization.

## Current status

The first authorized attempt ran on September 15. The pinned Qwen model loaded
successfully and all nine public reference benchmark points passed. Agent
startup then failed because the original state root produced an overlong Unix
socket path. No model API calls, candidate optimization, or hidden evaluation ran.
Evidence is retained in `evidence/solo_pilot/20260915-first-solo`.

The fix checks socket paths before spend admission. It was deployed and passed
the real OpenCode/OCI no-spend pilot on September 21 with a short state root.
Commits `f0316df` and `b374650` are pushed. The original approval has expired,
and the original journal remains consumed: no automatic retry or refund is
authorized. A future attempt needs reviewed budget reconciliation, refreshed
readiness evidence, and fresh spending authority.
The September 21 report attributes $0.23026748 to the reference app; this is not
complete invoice reconciliation and does not release reserved capacity.
The retry capacity check is retained in
`evidence/solo_pilot/20260915-first-solo/retry-budget-review.md`. Even optimistic
failed-attempt reconciliation leaves insufficient Modal capacity while the
unresolved qualification allowance remains held. No retry has started.

### Explicit September 21 retry

The user subsequently approved a $14 cumulative Modal allowance and $3
OpenRouter allowance, with the provider-side Modal cap unchanged. The original
plan and every admission receipt remain immutable. An explicitly pinned v2
authorization can reference one `exploratory-solo-retry/v1` amendment in the same
journal. The amendment preserves all prior Modal allowances and releases only
the original $2.90 model allowance, after verifying the retained aborted audit
and the digest-bound, empty model reservation database. It does not treat
missing qualification billing as zero.

The amended journal has $10.70176 Modal and $2.95 OpenRouter available before
the retry, exceeding the required $10.19296 and $2.90. A write-once amendment
marker prevents reopening it with the old plan alone or another amendment.
The new attempt has distinct, one-use admission keys and a new run directory;
neither a crash nor a new run ID grants another retry. This remains exploratory
execution, not registered or multi-condition experimentation.

That retry ran and aborted during reference evidence persistence, before any
model calls. Its reference measurement ID collided with the earlier run's remote
evidence destination. The local correction gives each measurement store a
durable remote namespace. See `evidence/solo_pilot/20260921-solo-retry` for the
failed audit, reported $0.22239952 app cost, and cleanup confirmation. The
one-use retry approval is consumed; no further attempt is authorized.

The user subsequently authorized reconciliation and one further run within the
same $14 Modal/$3 OpenRouter caps. A v2 retry amendment links the previous
amendment, reference-only failure audit, clean run configuration, and app billing
report. It retains the qualification and first-reference allowances and the
original $1 overhead reserve. It settles the failed retry's reference to its
reported $0.22239952 plus a $0.10 buffer, and releases that retry's unused $2.90
model and $1 extra-overhead allowances. The resulting headroom is $10.37936048
Modal/$2.95 OpenRouter, sufficient for the unchanged full-run allowances.
The previous amendment and every receipt remain immutable; a second write-once
marker prevents either earlier authority from reopening capacity. This is
operator-reviewed exploratory reconciliation, not final invoice settlement.
