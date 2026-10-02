# October 2 solo attempt: reference collector interruption

One exploratory solo attempt ran under the approved cumulative admission
ceilings of $17 Modal and $3.10 OpenRouter. The V7 amendment verified the
September 25 aborted run, its reconciled model receipts, Modal billing, and
the October 1 CPU-only collector evidence before releasing unused allowances.
It retained the older unresolved $0.76608 Modal reserve. The dedicated VM ran
clean source commit `f5ea74212535a29dce73c412b7d807d834219d80`.

The VM's kernel had changed from `6.8.0-139-generic` to
`6.8.0-142-generic`. The Podman identity matched the earlier pin when only
the kernel field was substituted, and three real no-provider-spend OCI
lifecycle tests passed. A replacement sandbox identity and one-use
authorization were retained before execution.

The public reference call was dispatched, but the collect-only Modal client
failed with a local `grpclib` connection error before terminal evidence was
confirmed. The run aborted at the reference stage. Its audit is
`status=aborted`, `scoreable=false`, digest
`sha256:1b055e27b062de5a6f07b90a5f62dc13e365864fb7748398e79532037188c109`.
No agent candidate or hidden evaluation ran. Abort cleanup requested
cancellation of the dispatched call but did not verify a terminal outcome;
its full reserved allowance remains held. All five Modal apps from the
attempt were stopped, no actor container remained, and the VM was stopped.

The post-abort journal has 50 receipts and shows $8.62937397 Modal and
$0.08565586 OpenRouter unreserved under the cumulative admission ceilings.
These are allowances, not provider charges. The same-day Modal billing
snapshot was incomplete when checked; it showed only one $0.08628110 app
row. No unused allowance from this aborted attempt has been released.

The original evidence and authorization are retained privately under
`.private/evidence-solo-connected-1002` and
`.private/solo-connected-1002`. A narrow no-spend regression now treats the
specific collect-only connection interruption as nonterminal and polls the
same call ID again, without redispatching GPU work. This fix is locally
tested, not live-proven. The one-use approval is consumed; another attempt
requires complete billing/dispatch reconciliation and fresh authorization.
