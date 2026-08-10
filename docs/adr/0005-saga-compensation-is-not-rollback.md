# 0005 — Saga compensation reports what it could not undo

- **Status:** Accepted (recorded retroactively 2026-08-05)
- **Date:** 2026-08-05

## Context

Four steps in this workflow have side effects outside the process. Their reversibility is not
uniform, and the differences are financial and regulatory rather than technical:

| Step | Execute | Reversal |
|---|---|---|
| `pull_credit` | hard inquiry on all three reports | **none exists** — permanent |
| `submit_to_aus` | DU casefile submission | none needed — read-only |
| `order_appraisal` | $600 to the AMC | refundable **pre-inspection only** |
| `lock_rate` | pricing exposure | releasable, but relock is at **worst-case pricing** |

A compensation layer that reports "rolled back" for all four would be lying about two of them.

## Decision

Compensation unwinds in reverse order and records, per step, one of: compensated, `none_possible`,
or `skipped`. "We could not" and "we did not need to" are different facts about the world and are
never merged. Where the appraiser has already been out, the ledger records the $600 as *not
recovered*.

A saga here is a sequence of business-level apologies, and some apologies cost money.

## Consequences

- The saga report is an honest artifact and can be shown to an examiner.
- Demo scenarios must include a case where compensation fails to restore prior state (scenario 3),
  because a demo where everything unwinds cleanly teaches the wrong model.
- `pull_credit` being non-compensatable makes idempotency load-bearing rather than a nicety — a
  retry that re-pulls is a permanent second inquiry on a borrower's report.

## Evidence

- `flow.py:85-97` — `pull_credit` registered `compensatable=False, compensate=None`, with the
  reason in `compensation_note`.
- `flow.py:107-121` — `order_appraisal` (pre-inspection only) and `lock_rate` (worst-case relock).
- `flow.py:484-488` — `order["inspection_complete"] = True` is set once the appraiser has been out,
  so compensation reports the truth rather than a refund.
- `core/saga.py` — `compensate_all` reverse ordering and the `none_possible` / `skipped`
  distinction.
- `flow.py:14-20` — the module docstring states the four steps and their real cost.

## Alternatives considered

**A generic rollback abstraction.** Rejected — it would force a uniform interface onto four steps
whose reversibility differs in kind, and the uniformity would be achieved by discarding the
information that matters.
