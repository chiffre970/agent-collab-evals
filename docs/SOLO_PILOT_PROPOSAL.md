# First solo pilot: deployment and budget proposal

Prepared September 12, 2026; updated September 14. The user approved local VM setup and the US$15
gross usage envelope, conditional on deployment, route, resource, and spending
checks passing before paid execution. Execution remains disabled. This document
is not a durable run-bound authorization receipt.

## Recommended deployment

Use a disposable local Linux VM through Lima, with the controller and rootless
container tooling inside the VM. Run OpenCode through Podman in the network-disabled OCI
actor container, connected to the controller's dedicated Unix sockets. Keep the
Qwen target and all GPU evaluation on Modal L4. This follows ADR 0002 without
introducing a cloud-hosting account or a remote agent transport.

Use four vCPUs and 8 GiB RAM; the host has 24 GiB RAM. Copy only the required
project files; disable default host-home mounts and credential/SSH-agent
forwarding. Credentials and private evaluator material
belong to the trusted controller, never the actor's mounts or environment. Do
not mount the Docker socket into the actor container. Keep controllers and broker
sockets inside the same Linux VM rather than trying to bind Mac sockets remotely.

Lima supports Linux VMs on Apple Silicon and Docker integration. Rootless Docker
needs cgroup v2, systemd, and delegated resource controllers for effective CPU,
memory, and process limits. These are deployment requirements, not guarantees
from merely selecting a template. Sources: [Lima](https://lima-vm.io/docs/),
[Lima Docker integration](https://lima-vm.io/docs/examples/containers/docker/),
[Docker resource limits](https://docs.docker.com/engine/security/rootless/tips/#limiting-resources).

Lima 2.2.0 is installed. The versioned bootstrap configuration is
`config/deployment/solo-lima.yaml`: pinned Ubuntu 24.04 ARM64 image, four vCPUs,
8 GiB RAM, a 40 GiB virtual disk, no host mounts, no SSH-agent forwarding, no
proxy-environment propagation, and no automatic application-port forwarding.
Lima's management SSH connection remains available. The Docker installation
uses the official bootstrap process; its resulting engine must still be pinned
and qualified. This is a local VM, with no cloud-VM charge.

The VM booted Ubuntu kernel `6.8.0-134-generic` and rootless Docker 29.8.0.
Docker reports systemd/cgroup v2 with delegated CPU, memory, and process
controllers. A UID-1000, network-disabled base container reported the expected
2-CPU, 4-GiB, zero-swap, and 256-process cgroup values. The minimal host environment
also connects to the rootless daemon. These observations are retained in
`evidence/deployment/local-oci-bootstrap-20260912.json`; they do not qualify
the complete OpenCode boundary or authorize execution.

Deployment progress:

1. **Complete: mapped identity and dependency remediation.** Rootless Podman
   4.9.3 uses `keep-id` to preserve UID 1000 on private mounts. A `0700` workspace
   is accessible without ACL repair or relaxed permissions; CPU, memory, swap,
   and process limits remain unchanged. Only three transitive packages changed:
   `fast-uri` 3.1.7, `hono` 4.13.7, and `qs` 6.16.0. The updated lock and rebuilt
   image report zero npm advisories. OpenCode remains 1.18.19.
2. **Complete: full no-spend container session.** The existing pilot ran real
   OpenCode through Unix model/candidate brokers, public feedback, selection,
   all hidden phases, reconciliation, and normal teardown. It made five synthetic
   model calls and 12 synthetic compute executions, with zero external calls
   or spend. No containers remained. The retained observation is
   `evidence/deployment/oci-solo-conformance-20260913.json`; the full generated
   evidence remains private under `tmp/oci-pilot-conformance/ace-z__8hnoz`.
3. **Complete: bridge-client interruption cleanup.** Each launch receives a
   host-generated container name. Teardown removes only that container and
   verifies absence, even after the attached client exits. Both the normal
   and interrupted no-spend pilot tests passed. The interrupted container
   outlived its client; the production abort path removed it and retained an
   unscoreable, aborted result. Its checkpoint was unavailable, recorded as a
   shutdown error. The running container's cgroup limits also matched the
   profile. Evidence: `evidence/deployment/oci-solo-cleanup-20260914.json`.
   Controller/VM loss and interruption during container creation remain outside
   this check; the cleanup handle is held by the live controller.
4. **Complete offline: resource settings and shared dollar admission.** GPU
   functions request and limit CPU to four physical cores and memory to 16 GiB,
   with an explicit 600-second container startup timeout. Evidence helpers use
   one core and 1 GiB. All 12 evaluation allowances now match the 1,800-second
   function timeout. A shared, append-only journal accounts for qualification,
   overhead, and pilot allowances before durable compute authority is issued.
   Model capacity is reserved before reference compute. Failed attempts do not
   refund capacity; automatic run restart is disabled. Tests exercise all 12
   requests against the real durable authorization service without dispatch.
5. **Complete: bounded provider and device/cancellation qualification.** On
   September 14 the current DeepInfra route passed three live probes for
   $0.00004386, with valid receipt reconciliation and zero reported cached tokens.
   Two resource-bounded Modal calls passed the L4 and running-call cancellation
   checks; observed runner/input/backlog counts returned to zero. Actual Modal
   cost remains unresolved because the immediate billing summary was unchanged.
   The full scored evaluator boundary is not covered by this small check.
6. **Next: scoped live execution gate.** The command still rejects live execution
   unconditionally, and the factory requires a registered OCI profile. Wire one
   run-bound exploratory authorization to the existing lifecycle and retained
   qualifications without labeling development conformance as registered. Finish
   the actual model-serving evaluator prerequisites before spending on the pilot.

The current image is
`docker.io/agent-collab/opencode-runtime@sha256:8c0242a1761cda906ba87127d44686871f1b8c8afaa1d6eda369c1a05fc61e21`.
The September 12 image and bootstrap record are historical, not the current
dependency baseline. The existing VM now has Podman and controller prerequisites
in addition to the original bootstrap. A fresh controller checkout needs its
Python package, Modal 1.5.4, a Node executable, and the locked runtime packages.
The controller's package metadata checks still require local `node_modules`;
the VM reuses those packages from the image. No credentials are needed for this test.

Run the no-spend acceptance test inside that VM's project checkout:

```sh
RUN_OCI_PILOT_INTEGRATION=1 .venv/bin/python -W error::ResourceWarning \
  -m unittest tests.test_oci_pilot.OciPilotIntegrationTests -v
```

The idle VM was stopped after setup to release RAM; its disk and image remain.
The image exists only in the local VM; it was not pushed to a registry. The
bootstrap observation is not a signed or independently resolved conformance
receipt. Registered deployment conformance remains incomplete despite the
successful development session. Use `limactl start agent-collab-solo --tty=false` to resume
the existing VM; do not create another instance for the next step.

## Provider recommendation

Keep the dated Flash model and DeepInfra FP8 through OpenRouter for this pilot.
Do not change provider within the attempt. The September 12 public endpoint
catalog lists these prices per million tokens:

| Provider | Input | Output | Note |
| --- | ---: | ---: | --- |
| DeepInfra | $0.06 | $0.18 | Existing development route; cached input $0.015 |
| OpenInference | $0.03 | $0.07 | Cheaper, but not the existing qualified route |
| Relace | $0.04 | $0.08 | Different FP4 quantization |
| BaseTen | $0.13 | $0.26 | More expensive in this snapshot |
| Fireworks | $0.22 | $0.66 | More expensive in this snapshot |

Source: [OpenRouter endpoint catalog](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-flash-0731/endpoints).
The unmodified JSON body plus a final newline is retained at
`evidence/provider_qualification/sources/openrouter-endpoints-20260912-budget.json`,
SHA-256 `7a639072a950a89299130d4b704a0e54b47475a7c3a5cd89716b4d6093adb6ad`.

The public [ZDR catalog](https://openrouter.ai/api/v1/endpoints/zdr) also listed
`deepinfra/fp8` for this model, with `supports_implicit_caching: false`. This was
a metadata check only; its complete raw response was not retained, so it is not
a replacement qualification receipt. Latency and throughput fields were null.
That September 12 observation was not authenticated qualification. The September
14 live qualification now verifies the account route and receipts, with probe
latencies of 12.035, 20.358, and 14.565 seconds. The OCI pilot now references a
separate September 14 gateway and billing catalog: $0.06 input, $0.015 cached
input, and $0.18 output per million tokens. Historical profiles remain unchanged.

## Conditionally approved budget: US$15 gross usage

The user confirmed on September 14, 2026, that Modal workspace `chiffre970`
is dedicated to this experiment. The supplied dashboard shows a saved $12.50
gross usage cap and $0.54 cycle usage. Read-only CLI billing also confirmed that
baseline after the free-storage adjustment, before credits. Cutoff behavior has
not been exercised. The OpenRouter key has no provider-side limit; its gateway
and shared journal bound requested work.

- Modal: $12 total, including setup, one bounded cancellation qualification,
  the public reference/candidate, hidden evaluations, and cleanup overhead.
- OpenRouter: $3 total for route qualification and the one agent attempt.
- Local VM: no cloud hosting charge. Credit-purchase fees, tax, and currency
  conversion are outside this usage estimate and require separate consideration
  if a top-up is necessary. Do not purchase credits automatically.

Published Modal task rates are $0.000222/L4-second, $0.0000131/physical-core-second,
and $0.00000222/GiB-second, checked September 14, 2026. The live pilot now
reserves 21,600 function seconds, including 18,000 hidden seconds. The offline
estimate includes 600 startup seconds per GPU invocation, a 60-second margin,
and one bounded CPU evidence-persistence invocation per execution. It reserves
$0.76608 per evaluation and $1 of shared overhead: $10.19296 for all 12
evaluations, leaving $1.80704 of the Modal envelope for qualification. Overhead
is reserved for setup, warm-up, storage, and cleanup; it is not measured usage.
Source: [Modal pricing](https://modal.com/pricing).

These are admission estimates, not invoice guarantees. CPU throttling is soft;
memory limits are hard. Execution timeouts exclude container startup and can
overshoot. GPU preemption can restart work independently of the application's
retry policy. Repeated evidence-helper dispatch and platform restarts therefore
remain covered by the required outer provider usage cap, not a claim that one
local reservation proves a maximum invoice.
[Modal resource configuration](https://modal.com/docs/guide/resources),
[Modal timeouts](https://modal.com/docs/guide/timeouts),
[Modal preemption](https://modal.com/docs/guide/preemption).

`config/pilots/solo-spend-envelope-v1.json` pins a conservative $11.95 Modal
allowance and the approved $3 OpenRouter allowance. All future
qualification and pilot operations must use the same host-private
`PilotSpendEnvelope` directory. Receipts are immutable and fsynced under a
cross-process lock; retries cannot alter an operation's allowance. The production
live factory requires a concrete `PilotSpendGuard`, rather than an arbitrary
authorization callback. This journal assumes a trusted host and is not an
independently anchored billing ledger or registered-study authority. Do not
delete it or create a fresh directory to recover budget. The existing provider
route and model-gateway qualification commands now require `--spend-envelope`,
reserving $0.05 and $0.01 respectively. Each command is one-shot for that journal;
failure keeps its reservation and a repeated invocation fails before provider
access. Run them on the pilot's controller, not against separate Mac/VM journals.
The existing Modal access preflight now uses the same journal for GPU
qualification. It reserves $1.53216 before either of two calls: a device check
and a running-call cancellation check. Both calls have explicit CPU, memory,
startup, and execution limits, no secrets or volumes, and blocked network access.
It retains dispatch intent, exact call IDs, results, cancellation requests, and
resource observations. Failed or ambiguous dispatch and cleanup stop the
sequence without refund or automatic retry. Function statistics are observational;
they do not prove final billing or qualify the full scored evaluator boundary.
The qualification plus the planned pilot reserve $11.72512, leaving $0.22488
unallocated under the local Modal allowance.

The OCI pilot's model allowance is $2.90, leaving $0.10 for route qualification
within the $3 total. Neither adapter construction nor the offline cost profile
verifies a provider billing cap or opens the operator gate.

Use gross usage before promotional credits when comparing experiments. Modal
workspace budgets apply to the billing cycle, not one run. Environment budgets
require Team/Enterprise. The installed billing-summary command worked on this
Starter workspace; do not assume every billing endpoint has the same availability.
Do not upgrade a plan for this pilot. If necessary, retain dashboard billing
evidence after it settles and label unresolved cost explicitly.
[Modal budgets](https://modal.com/docs/guide/budgets),
[Modal billing](https://modal.com/docs/guide/billing).

On September 14, the user supplied dashboard evidence that the dedicated
`chiffre970` workspace's gross **Usage limit** is saved at $12.50, with $0.54
displayed usage for September 1–October 1. The $11.95 local allowance leaves a
margin below the displayed $11.96 headroom. A read-only CLI summary returned
$0.83550918 metered cost, a $0.29550918 free-storage adjustment, and $0.54 in
credits. Neither that credit nor the $0 invoice erases experimental usage.
Retained observations are in `evidence/deployment/modal-usage-cap-20260914.json`
and `modal-billing-summary-20260914.json`. This verifies the saved setting, not
real-time cutoff behavior. Recheck the baseline before dispatch and after a
billing-cycle change. The bounded paid qualifications have now run; do not repeat
them or recreate their spending journal.

The September 14 read-only OpenRouter refresh is retained as
`config/provider_qualification/openrouter-deepseek-v4-flash-zdr-20260914T083745Z.json`,
with raw endpoint and ZDR responses resolved by its source manifest. DeepInfra
remains the cheapest listed candidate for the declared input/output mix. Its
listed uncached input price is now $0.06 per million tokens; output remains
$0.18 per million. The OCI pilot references the new dated gateway and billing
profile, which passed live qualification. The resolved selection is
`config/provider_qualification/deepseek-v4-flash-deepinfra-development-selection-20260914.json`.
Its exact provider receipts independently reconcile $0.00004386 in charges.

## Execution boundaries

- Approval covers one solo attempt only, not peer comparisons or automatic
  reruns. Qualification failures stop the sequence before the attempt.
- Reserve enough remaining budget for the next complete operation and cleanup
  before dispatch. Stop before admitting work that exceeds the envelope.
- No automatic credits, plan upgrades, provider fallback, or new cloud hosts.
- A cancellation acknowledgment is not proof of terminal resources or final
  billing. Unresolved dispatches stop further paid work and require review.
- The live wiring enforces admitted allowances, not provider billing. Verify
  the workspace gross-usage limit and its current-cycle baseline before opening
  the operator gate. A net spend limit after credits is not the requested gross
  usage cap. Workspace limits affect every application in that workspace; do
  not change them without confirming scope. The historical Darwin configuration
  retains null limits.

Next action: connect the existing live lifecycle to one run-bound exploratory
authorization, preserving the registered-study gate, then finish the full
model-serving evaluator prerequisites. Qualifications are retained at
`evidence/deployment/solo-qualification-20260914`; that directory is a read-only
snapshot, not the authoritative journal. The authoritative VM journal has
$10.41784 Modal and $2.95 OpenRouter admission capacity remaining. No further
approval is needed for the scope
already granted. New paid hosts, top-ups, upgrades, reruns, and peer comparisons
remain outside that scope.
