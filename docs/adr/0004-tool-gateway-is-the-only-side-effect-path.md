# 0004 — Agent and Flow tool calls share one gateway

- **Status:** Accepted (recorded retroactively 2026-08-05)
- **Date:** 2026-08-05

> **Implementation clarification (2026-08-10):** This ADR scopes the gateway to tool-exposed
> external/business effects. Saga compensation, idempotency, policy enforcement,
> and database persistence remain dedicated layers.

## Context

Three concerns — error classification, tool-call accounting, and menu scoping — would otherwise be
duplicated across every agent and every flow step. Duplicated controls drift, and a control that
holds in seven places out of eight is not a control.

There is also a subtler requirement: a compliance refusal must not be indistinguishable from a
vendor failure. If a policy violation came back to an agent as text, the agent would rephrase and
retry, and "an agent that cannot do this" would degrade into "an agent that was told no once".

## Decision

Every tool-exposed external/business effect is wrapped by `gateway()`. Tool-exposed deterministic
calculations use the same wrapper. It records the call in a
run-local ledger against a named actor, and it classifies errors on exactly one axis:

- `VendorError` → returned to the caller **as text**, because a bureau timeout is worth reasoning
  about (retry, alternative source, or record as a condition).
- `PolicyViolation` → **re-raised untouched**, because it is a control and not a guardrail.

Agents and the Flow use the same gateway. There is one, not two.

## Consequences

- Tool-call precision and recall become measurable properties rather than anecdotes.
- The ledger is run-local via a `ContextVar` holding a mutable object bound once on the kickoff
  thread — necessary because CrewAI dispatches each `@listen` as its own asyncio task, and a task
  gets a *copy* of the context. This is subtle and must not be "simplified" back to two plain
  `ContextVar`s.
- The gateway is only as good as the ledger's actor attribution. Two known gaps are recorded in
  [ADR-0011](0011-observed-telemetry-not-self-report.md).

## Evidence

- `tools.py:131-157` — `gateway()`, including the deliberate non-catch of `PolicyViolation` at
  `:141-144`.
- `tools.py:58-118` — `RunContext` and the `_LedgerProxy`, with the asyncio-copy reasoning.
- `crews/_base.py:29-48` — `as_tool()` adapts the *gateway* function for CrewAI, so an agent's call
  is accounted identically to the Flow's.
- `tools.py:314-327` — `tools_for_income_analyst()`, the deterministic menu filter.
- `evals/run_evals.py:247-289` — what the ledger makes computable.

## Alternatives considered

**Per-agent tool definitions.** Rejected: it puts the compliance gate behind whichever agent
remembered to use the wrapped variant.
