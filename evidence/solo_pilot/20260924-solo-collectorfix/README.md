# September 24 solo attempt: hidden-series abort

This was one authorized exploratory solo attempt, not a scored experiment. The
run audit is `status=aborted` and `scoreable=false`. The complete run directory
is retained in the stopped Linux VM at
`/home/rmh.guest/ace-runs/solo-collectorfix-0924`, with an ignored private
host copy at `.private/evidence-solo-collectorfix-0924`. Hidden workload,
responses, and scores are not published here.

- Source commit: `d3fe34504dd805cd87e669dcad86ef1f8f3e6988`.
- Frozen run configuration digest:
  `sha256:fd7b8fe3ebba8bdd67d53723f12a48ab9c37b3cd0db8de96d38d8fe5527412b7`.
- The public reference and one candidate completed. The candidate was eligible
  and selected in public evaluation, but this is not a hidden or comparative
  result.
- Hidden correctness and the first paired quality repetition produced retained
  receipts. The second quality repetition failed before a scored GPU call was
  recorded. The Modal runner looked for the previous repetition under the new
  request-bound measurement ID, while the controller had stored it under the
  prior request's ID. Cleanup recorded that attempted dispatch as unresolved;
  no dispatch record exists for it.
- The model budget reconciled with seven settled calls and $0.00510996 charged.
  Modal's September 24 report, checked later the same day, attributed
  $0.88708654 to apps created by this attempt. This remains a provider-billing
  snapshot, not a final invoice; a separate no-GPU conformance helper cost
  $0.00004471. No Modal app or agent container remained active, and the VM was
  stopped after evidence retention.

The local follow-up makes controller-managed quality and performance repetitions
use their request-bound measurement IDs without applying standalone series
predecessor checks to the wrong directory. Standalone series retain those
checks. No-GPU regression tests cover repetition two of both paths, and the
full local suite passes. These changes do not make the aborted run scoreable or
authorize another paid attempt.
