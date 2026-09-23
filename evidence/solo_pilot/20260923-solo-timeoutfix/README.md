# September 23 collector-aborted solo attempt

This was one authorized exploratory attempt, not a scored experiment. Its
original audit remains `status=aborted`, `scoreable=false`, with failure at
`reference`. No agent, model-provider call, candidate, or hidden evaluation
started. The run directory is retained in the stopped Linux VM at
`/home/rmh.guest/ace-runs/solo-timeoutfix-0923`, with a private host copy at
`.private/evidence-solo-timeoutfix-0923`.

- Source commit: `9312b743d256dfbae87fd4e117e68bca31abb75c`.
- Frozen run configuration digest:
  `sha256:b55e4ed0e8ba970882f05909ddd67c59f95dfdc1ec415ab4e33040de22272816`.
- The single reference GPU call was `fc-01M36RFHNVSH1NPN1NR2YSMBW4`. The
  collector failed after 633.132 seconds with a Modal `ConflictError` saying
  its function was stopped; it never committed a local measurement bundle.
- Read-only recovery of that same call ID returned a completed staging pointer.
  The staging volume contains a receipt with `ok=true`, nine benchmark-point
  files, and 669.360 seconds of function-body time. The receipt SHA-256 is
  `f5b8762829e680cddbda84a5b9197528521700dd1707e0b5d299c1fcbe3a3a13`.
  It matches the staging manifest; all nine raw-file digests also match.
  This is forensic evidence, not a local scored result.
- The compute ledger remains `dispatched` without terminal evidence. Cleanup
  recorded `cancellation_requested`, not terminal confirmation. A subsequent
  read-only function-call query returned its completed pointer. Neither the
  ledger nor the aborted audit was rewritten.
- Modal's September 23 billing report attributed $0.20902658 to the scored
  reference app. No collection helper charge appeared in that report. Billing
  may settle later. After abort, Modal listed no active apps, Podman listed no
  running containers, and the dedicated VM was stopped.

The observed failure is in post-run evidence collection, not in the GPU
benchmark. A later local change limits each collect-only app lease to 60
seconds and treats a stopped collector as nonterminal so another collection
attempt can reattach to the same call ID. That change passed local tests but
has not been validated against a live Modal call. It never authorizes another
GPU dispatch. The consumed one-attempt approval must not be reused.
