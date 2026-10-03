# October 3 solo collector environment failure

The CPU-only staging-bundle diagnostic passed before one authorized solo
attempt. The V9 amendment reconciled the prior abort against its retained
budget receipts and Modal billing snapshot, without changing the prior audit.
The new attempt, `solo-stagingfix-1003`, aborted at its first public reference
collection. It is not scoreable; no agent or hidden evaluation ran.

The reference call `fc-01M40PAXQ6E8RD3GEYG3TQYTSG` returned a terminal
`modal-evaluator-staging-bundle-pointer/v0alpha1`. The collector nevertheless
reported that the bundle manifest did not exist. The scored call had written
to the `dev` staging Volume. The collector's minimal subprocess environment
omitted `MODAL_ENVIRONMENT`, so its `Volume.from_name` lookup used the default
`main` environment. Modal created a same-named empty `main` staging Volume
at the failure time. A read-only retrieval of the exact terminal pointer
and all nine raw reference documents succeeded with `MODAL_ENVIRONMENT=dev`;
it failed without that environment binding before the code correction.

The local correction carries the profile-pinned `dev` environment into all
Modal dispatch and collector children. The evaluator explicitly binds its
Volumes and secret to `dev` and no longer creates Volumes implicitly.
Read-only collection of the retained call also succeeds with the patched
evaluator when the host environment variable is absent. A child process on
the dedicated VM, launched through the patched minimal environment, verified
the same terminal pointer and nine raw documents. These checks made no new
scored dispatch, GPU call, or model call. The local suite at that checkpoint
ran 420 tests: 397 passed and 23 live-integration tests were skipped.

The actual trusted-client collector subsequently completed the full persistence
and normalization path against that retained call. All nine raw documents were
verified and the normalized reference was valid. The conformance receipt is at
`/home/rmh.guest/agent-collab-evals/.private/dev-persistence-1003-v2/conformance.json`,
digest `sha256:87bdc44322730de5590d158b8fbee68f8868ece774b7d2dae7e645081d1a6d90`.
It created no scored dispatch or model call and did not alter the original audit
or ledgers. This proves evidence-copy and normalization, not end-to-end score
closure.

The original audit remains at
`/home/rmh.guest/ace-runs/solo-stagingfix-1003/audit.json` with digest
`sha256:2b6502eca0e1d1b6b975cc2dc86724f49357c314e0a5eb10dbec32ae4a0bc79f`.
Its original cancellation request is not terminal cleanup or settled billing
proof. Subsequent retained observations confirm this call returned its terminal
bundle and the older reserved call `fc-01M3XVBN06YC79HA1EACHJJTVA` was cancelled.
Hourly bills for their stopped apps total $0.22915129 and $0.08628110,
respectively. The V10 evidence-bound settlement keeps a $0.10 buffer per app,
releases only the terminal attempts' unused Modal allowances and the latest
entirely unused model allowance, and preserves all earlier evidence.
The new one-use decision permits `solo-devbound-1003` under the unchanged
$20 Modal / $3.10 OpenRouter cumulative ceilings. Preparation must verify the
settlement and complete-run capacity before issuing execution authority.

Modal documents that Volumes are environment-scoped and that changes must
be committed or reloaded for cross-container visibility. See its
[Volume guide](https://modal.com/docs/guide/volumes) and
[Volume API](https://modal.com/docs/sdk/py/latest/Volume).
