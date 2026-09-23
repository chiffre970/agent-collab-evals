# September 23 exploratory solo attempt

This is an aborted development pilot, not a scored experiment. The original
run remains in the VM at `/home/rmh.guest/ace-runs/solo-next-0923`; a private
copy is retained at `.private/evidence-solo-next-0923`. Neither was edited to
make the attempt appear complete.

- Source commit: `ad75c700dfbdc0986950cd6c4e43834eb952f705`.
- Frozen run configuration: `sha256:2c3c2c237378f03b4d5ffcc1b0be18f0999f1b05aad488213b187b25ef2aeba2`.
- Audit: `status=aborted`, `scoreable=false`, failure at `public_feedback`;
  `cleanup_failure=ExceptionGroup`. Hidden evaluation did not start.
- The agent submitted one candidate. Both single-run public measurements were
  eligible. Stock vLLM scored 999,562 ppm; the candidate scored 993,918 ppm,
  5,644 ppm (about 0.56%) lower. This is diagnostic, not a hidden or
  multi-repetition outcome.
- The independently loaded local measurement bundles had receipt SHA-256
  digests `07e4096297f6f8f839604e49edbdc0e6d8950d8ae9e04fe464fd65c1ae09d99c`
  (reference) and `04159cc3d66c5ccbbbb2c354465be670ec94d82140b94780c4b76ceb5edddabe`
  (candidate). The candidate public compute used 671 seconds; hidden compute
  used zero seconds.
- The model budget ledger reconciled all three calls against provider
  receipts: $0.00308172 charged, no active reservations, forfeits, overruns,
  or missing receipts.
- Modal's September 23 billing report, queried after abort, listed $0.23874382
  for the reference app, $0.21029472 for the candidate app, and $0.00010425
  across two collection helpers: $0.44914279 total. Billing may settle later.
  All Modal apps were stopped, and no sandbox container remained active.

Podman recorded the agent container starting at 12:51:08 AEST and exiting with
code 124 at 12:56:09 AEST, almost exactly at the 300-second limit in the
versioned development OCI profile. The candidate benchmark continued outside
that container and completed, but the controller could not deliver feedback to
the dead bridge. A new OCI profile with a sufficient lifetime and a fail-closed
schedule check supersede that profile for future executable pilots. The
historical profile and this run's audit remain unchanged.

After this attempt, commit `2e312ea` passed 399 local tests (376 passed,
23 skipped). Its new OCI profile completed a deployed, no-spend OpenCode/Podman
pilot in the Linux VM with no container left behind; the VM also passed the
short-profile rejection test. This does not retroactively validate the aborted
paid attempt or prove a seven-hour container lifetime.
