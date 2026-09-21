# Retry budget review

Reviewed September 21, 2026. This report grants no execution authority and
does not release or replace any admission receipt.

## Verified outcome

- Commits `f0316df` and `b374650` are pushed to `origin/main`.
- The retained failed-attempt budget database contains zero model reservations
  and zero charged USD nanos. It was queried read-only.
- Modal's September 1–22 billing report attributes $0.23026748 to the reference
  app `ap-prLVu6B1U3GH3DTD9D8CzI`, consistent with the retained narrower report
  in `billing-report-20260921.json`.
- The full-month report contains no row for qualification app
  `ap-MWBSQb6OU0ns6Hr8TU3EA0`. Absence is not evidence of zero cost.
- No new model or GPU execution was started. The authoritative VM journal is
  unchanged; evidence copies are not spending journals.

## Capacity check

The approved cumulative caps remain $11.95 Modal and $3 OpenRouter. An identical
new pilot requires $10.19296 Modal and $2.90 OpenRouter in admission allowances.
The current journal leaves $8.65176 Modal and $0.05 OpenRouter.

Even an optimistic reconciliation that releases all failed-attempt overhead and
retains only its reported reference cost would leave:

```text
$11.95 cap - $1.53216 qualification allowance - $0.23026748 reference cost
= $10.18757252 available

$10.19296 required - $10.18757252 available = $0.00538748 short
```

This is a best-case capacity calculation, not a completed settlement. It does
not establish that all overhead is unused or all provider charges are final.
The qualification allowance remains held because its actual cost is unresolved.

## Next decision

Resolve qualification billing before releasing its allowance, or obtain explicit
direction on a revised cumulative spending envelope. Do not reduce safety
allowances merely to fit this calculation. Any retry also needs reviewed,
append-only reconciliation of the existing journal, refreshed readiness, and
fresh authority bound to one new attempt. The expired first-attempt approval and
single-attempt reservation keys must not be reused or bypassed.
