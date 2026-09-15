# Exploratory qualification evidence

This is a read-only copy of evidence from the trusted Lima controller, not a new
spending journal. The authoritative journal remains at
`/home/rmh.guest/agent-collab-evals/.private/solo-spend/first-solo` in
`agent-collab-solo`. Never resume spending from this copy or refund its allowances.

On September 14, 2026:

- The three-probe DeepInfra qualification passed, with $0.00004386 in
  provider-reported charges, zero reported cached tokens, and valid budget
  reconciliation. The exact receipts and resolved selection are retained under
  `evidence/provider_qualification` and
  `config/provider_qualification/deepseek-v4-flash-deepinfra-development-selection-20260914.json`.
- Two Modal L4 calls passed device and running-call cancellation checks in
  `dev`, app `ap-MWBSQb6OU0ns6Hr8TU3EA0`. The first returned an NVIDIA L4,
  23,034 MiB, driver 580.95.05. After each cancellation request, function
  statistics reported zero runners, running inputs, and backlog. The app
  completed. These are observations, not proof of final billing or conformance
  of the full scored evaluator.
- The journal reserved $0.05 OpenRouter and $1.53216 Modal, leaving $2.95 and
  $10.41784 respectively. The planned pilot requires $2.90 and $10.19296.
  Unused qualification reservations remain consumed.
- Credentials were passed over the local VM's management connection to the
  trusted controller process through stdin. No credential file was created;
  no actor container or Modal GPU function received those credentials.

The immediate Modal billing summary was unchanged from the pre-call summary;
the new usage had not appeared. Actual Modal qualification cost remains
unresolved. The OpenRouter key has no provider-side limit; the gateway and
shared admission journal bound its requested work. The Modal workspace has a
saved $12.50 gross usage cap, but cutoff behavior was not exercised.

The controller used commit `ba24b43` plus the dated billing/profile changes and
rate-binding check. The no-spend OCI pilot and forced bridge-stop cleanup tests
also passed on that controller, leaving no actor containers. This evidence does
not authorize registered experiments or remove the solo command's live gate.
