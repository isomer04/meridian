# 0002 — No hands-off autonomous planner

- **Status:** Accepted (recorded retroactively 2026-08-05)
- **Date:** 2026-08-05

## Context

The obvious alternative to [ADR-0001](0001-deterministic-flow-owns-control-flow.md) is to give a
single agent the tool set and let it plan the file end to end. This is the architecture most
"agentic" demos adopt, and it is worth recording explicitly why it was not adopted here rather
than leaving the absence unexplained.

## Decision

No component of this system plans its own sequence of steps. Autonomy is bounded to: choosing
within a tool menu that was filtered deterministically, and producing a typed judgment that a
router consumes.

## Consequences

Four capabilities depend on a fixed step vocabulary and would be lost:

1. **Resume from the ledger.** After a process death the flow state is gone; the saga log is not,
   and completed steps are rebuilt by name. There is no equivalent to "replay whatever the planner
   did last time."
2. **Idempotency.** The key is `sha256(loan_id | saga_step_name | attempt_epoch)` — keyed on step
   *name*.
3. **Tool-call precision as a metric.** The forbidden-tool set is a property of the file, not of
   the answer. Under a planner, "should this tool have been called" is unanswerable.
4. **The unconditional QC floor.** Deterministic compliance checks re-run after the QC agent
   precisely because the workflow knows QC runs last.

The cost accepted: the system cannot adapt to a file shape nobody anticipated. For a regulated
workflow with a finite set of loan programs, that is the correct trade.

## Evidence

- `flow.py:177-185` — `saga.restore()` rebuilds completed steps from `saga_log` alone.
- `core/idempotency.py` — step-name-keyed hashing.
- `evals/run_evals.py:247-289` — `metric_tool_calls`, precision and recall against a per-file
  expected/forbidden set.
- `crews/__init__.py:280-322` — the deterministic QC floor that runs regardless of agent output.
- `crews/__init__.py:158-160` — the empirical case: an agent's self-reported corpus routing was
  wrong (it reported an FHA file as routed to the agency corpus it never touched) and was replaced
  with a deterministic `route()` call. That is a contained instance of what unbounded autonomy
  generalises.

## Alternatives considered

**Planner with a validated action space** (the planner may only emit steps from a fixed
vocabulary, each gated). Genuinely defensible, and closer than full autonomy — but it buys
adaptivity this domain does not need while giving up the ability to state, in one file, what will
happen to a loan. Revisit only if the number of distinct program flows grows past what a Flow can
legibly express.
