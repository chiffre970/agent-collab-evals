# September 25 solo attempt: quality evidence collection abort

This was one authorized exploratory solo attempt, not a scored experiment.
The original audit remains `status=aborted` and `scoreable=false`. The complete
run is retained privately at `/home/rmh.guest/ace-runs/solo-seriesfix-0925`,
with an ignored host copy at `.private/evidence-solo-seriesfix-0925`. Hidden
workloads, responses, and scores are not published here.

- Source commit: `9711db8e6532a645063ff2dcf55189a0dcb1f3e8`.
- Frozen run configuration digest:
  `sha256:382c97662703157542ee843cf993017183f39bb83938c2a8c8b5067eb2578ade`.
- The stock reference, agent candidate, public feedback, and neutral selection
  completed. The single public candidate score was 999,925 ppm versus the
  reference's 998,524 ppm (about 0.14% higher). This is diagnostic only;
  it does not establish repeatable improvement or preserved hidden quality.
- Hidden correctness and three quality calls committed. The fourth quality
  call, the reference member of repetition two, finished its GPU work but
  failed during the collector's CPU persistence call with
  `ConflictError: function ... is stopped`. Six compute requests have terminal
  ledger evidence, one remained dispatched during abort cleanup, and five
  were never dispatched. The predecessor-check fix therefore passed its
  former pre-dispatch failure point.
- On September 29, read-only recovery retrieved that original call's terminal
  staging pointer and downloaded its existing staged output. The receipt and
  all 64 raw-file digests verified. Recovery started no compute and does not
  repair the aborted audit, score the incomplete series, or refund the journal.
- The model budget reconciled five settled calls at $0.00480084. Modal's
  September 29 snapshot attributes $1.16617552 to this attempt and reports
  $4.12990815 across the retained September billing period. These are usage
  snapshots, not final invoices. No Modal app or actor container remained active;
  the dedicated VM was stopped after retaining the evidence on September 29.

The follow-up keeps collection apps connected for their full local entrypoint;
only scored dispatch detaches. Public, correctness, and quality transports use
the same stopped-helper recovery path and a bounded 60-second collection wait
with time reserved for the CPU copier. A helper timeout or stopped-function
conflict leaves the original call nonterminal; the next poll collects that same
call ID without consuming another dispatch authorization. Other errors remain
fatal. Transport profile versions change with this lifecycle policy.

Focused regressions cover connected collection, detached dispatch, stopped
helpers, unrelated failures, and durable single-use spend authorization.
The full local suite ran 408 tests: 385 passed and 23 skipped. The lifecycle
change is locally tested, not live-proven. Another paid attempt needs a fresh
evidence-bound authorization; the September 25 one-use approval is consumed.
