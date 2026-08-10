# 0001 — A deterministic Flow owns control flow; models never do

- **Status:** Accepted (recorded retroactively 2026-08-05)
- **Date:** 2026-08-05

## Context

Loan origination combines three failure modes in one workflow: decisions that must be
attributable, arithmetic that must be exact, and side effects that are partly irreversible.

Two of the four saga steps cannot be undone in any meaningful sense. `pull_credit` places a
permanent hard inquiry on all three bureau reports. `lock_rate` can be released, but a relock is
subject to worst-case pricing, so compensation moves the borrower to a *worse* state than before.
A planner that can re-enter a step can re-pull credit.

There is also an ordering constraint that is legal rather than architectural: under 12 CFR
§1026.19(e)(2)(i)(A) no fee may be imposed before the consumer has received the Loan Estimate and
indicated intent to proceed.

## Decision

Control flow is owned exclusively by `OriginationFlow`'s `@start` / `@listen` / `@router`
methods. Every router branches on typed state — an enum, a bool, a float produced by `calc/` or by
a vendor. No router parses prose, and no model may choose which step runs next.

Where an agent informs a branch, it returns a **typed field** and the router branches on that
field, never on the surrounding rationale.

## Consequences

- The step vocabulary is fixed. That is a precondition for resume-from-ledger, for step-name-keyed
  idempotency, and for tool-call precision being computable at all.
- New behaviour requires editing the Flow, not prompting an agent. Slower to change; the point.
- The system cannot exhibit emergent multi-step planning. Accepted deliberately.

## Evidence

- `flow.py:307-312` — `route_after_intake` branches on `six_pieces.complete`.
- `flow.py:428-459` — `route_collateral` is the one agent-informed branch; the agent returns
  `exercise` as a bool and the router reads only that.
- `flow.py:608-618` — `route_decision` branches on the `Decision` enum, using an approval
  *allowlist*. The comment records the prior bug: testing `!= DENIED` routed `SUSPENDED`, `None`
  and `INCOMPLETE` into the approved branch.
- `flow.py:85-121` — saga steps, including `compensatable=False` on `pull_credit` and the
  worst-case-pricing note on `lock_rate`.
- `flow.py:266-305` — the disclosure step, which exists at that position for the regulation's
  reason, not a workflow preference.

## Alternatives considered

**A planner-driven agent loop.** Rejected: see [ADR-0002](0002-no-hands-off-autonomous-planner.md).

**Letting a router read the underwriter's rationale text.** Rejected — the moment a router reads a
sentence to find the path, the model owns control flow and every downstream guarantee in this
document is void.
