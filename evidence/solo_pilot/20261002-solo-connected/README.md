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

## Collector diagnosis

The retained dispatch identifies one scored reference call. Modal reported it
as canceled after abort cleanup. The call has no staged or durable result.
While that call was pending, the controller opened connected collector apps
about once per minute. The final collector's client connection failed, and
the controller aborted after about 291 seconds, well before the registered
30-minute reference execution allowance. This sequence identifies the
collector RPC as the immediate abort cause; it does not establish why the
underlying connection closed.

Read-only checks from the dedicated VM retrieved the canceled call's status
and a previously retained 7,356-byte staging manifest through Modal's SDK,
without creating an app or function. The manifest digest matched the
retained copy:
`sha256:1b5b6ffb7aef26a504cb7a06d00e4f32e65bfa05b0eaa2297f3975b5f4de576e`.
Modal documents [zero-timeout polling of an existing call](https://modal.com/docs/sdk/py/latest/FunctionCall)
and [client-side Volume reads](https://modal.com/docs/sdk/py/latest/Volume).

The development transports now use that call-status probe while a scored call
is pending. They start a connected collector only after the call returns or
reports a terminal error. The probe does not dispatch compute, consume an
additional compute authorization, or replace digest-checked evidence
collection. Public performance, hidden correctness, and hidden quality
transport profile versions changed. Unit tests cover pending, terminal,
expired, and transient client states, plus the no-collector path in all three
transports.

On October 2, a separate CPU-only Modal probe tested the new SDK status path
against a live 40-second call. The probe reported pending before completion,
verified the returned sentinel, and reported terminal afterward. Its one-use
$0.10 Modal allowance was added to the existing V7 journal, which now has 51
receipts and $8.52937397 Modal allowance remaining. It made no GPU or model
provider call. App `ap-i5Kqkpfdi0MiHOVeYltlI9` stopped with zero tasks;
Modal's current billing snapshot attributes $0.00015361 to it, subject to
later billing updates. This validates the status probe, not the complete
scored collection path or solo pilot. The prior aborted run remains
non-scoreable, and its unresolved reserve has not been released.
