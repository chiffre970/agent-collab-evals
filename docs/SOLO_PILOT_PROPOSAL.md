# First solo pilot: deployment and budget proposal

Prepared September 12, 2026; updated September 13. The user approved local VM setup and the US$15
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
3. **Next: remaining execution gates.** Qualify forced-stop cleanup and the
   remaining deployment boundaries; bind the approved dollar envelope, Modal
   resource ceilings, and current provider/billing qualification before paid
   execution. The live candidate remains disabled. The process-only
   `development_conformance` profile is explicitly rejected by the live factory.

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
No authenticated provider call was made; account routing and measured latency
remain unverified. The current billing catalog still uses $0.08 input and $0.016
cached input and must be refreshed through the existing evidence-backed process
before paid execution. Keep the current historical catalog immutable.

## Conditionally approved budget: US$15 gross usage

- Modal: $12 total, including setup, one bounded cancellation qualification,
  the public reference/candidate, hidden evaluations, and cleanup overhead.
- OpenRouter: $3 total for route qualification and the one agent attempt.
- Local VM: no cloud hosting charge. Credit-purchase fees, tax, and currency
  conversion are outside this usage estimate and require separate consideration
  if a top-up is necessary. Do not purchase credits automatically.

Published Modal task rates are $0.000222/L4-second, $0.0000131/physical-core-second,
and $0.00000222/GiB-second. The 13,200 planned evaluation seconds cost $2.9304 for
the GPU alone. Assuming four physical CPU cores and 16 GiB RAM throughout, the
combined illustration is $4.09; twelve full 1,800-second function timeouts would
be $6.69. A further 600 seconds per invocation adds about $2.23 under that same
resource assumption. These are calculations, not observed charges or hard upper
bounds. Source: [Modal pricing](https://modal.com/pricing).

The current functions do not pin CPU/memory ceilings, and some ledger allowances
are shorter than their function timeout. Pin resource requests/limits and include
startup, platform retries, storage, and cleanup in admission estimates before
treating the proposal as an enforceable envelope. Modal bills the greater of
requested and used CPU/memory; requesting a minimum is not a maximum.
[Modal resource configuration](https://modal.com/docs/guide/resources).

Use gross usage before promotional credits when comparing experiments. Modal
workspace budgets apply to the billing cycle, not one run; environment budgets
and programmatic billing reports require Team/Enterprise. Confirm the actual
account plan and prior cycle usage rather than assuming these APIs are available.
Do not upgrade a plan for this pilot. If necessary, retain dashboard billing
evidence after it settles and label unresolved cost explicitly.
[Modal budgets](https://modal.com/docs/guide/budgets),
[Modal billing](https://modal.com/docs/guide/billing).

## Execution boundaries

- Approval covers one solo attempt only, not peer comparisons or automatic
  reruns. Qualification failures stop the sequence before the attempt.
- Reserve enough remaining budget for the next complete operation and cleanup
  before dispatch. Stop before admitting work that exceeds the envelope.
- No automatic credits, plan upgrades, provider fallback, or new cloud hosts.
- A cancellation acknowledgment is not proof of terminal resources or final
  billing. Unresolved dispatches stop further paid work and require review.
- The current callback-based live wiring does not yet enforce the approved
  Modal dollar envelope. Implement and verify that binding before opening the
  operator gate. The OCI candidate records $12 Modal and $3 OpenRouter; the
  historical Darwin configuration retains null limits. Qualification and pilot
  usage must share this total envelope, not receive separate full allocations.

Next action: finish and verify the local deployment, then qualify the provider
and resource/spending controls. No further approval is needed for the scope
already granted. New paid hosts, top-ups, upgrades, reruns, and peer comparisons
remain outside that scope.
