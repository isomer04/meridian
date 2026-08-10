# 0011 — Agent behaviour is observed, never self-reported

- **Status:** Accepted — handoff remediation implemented
- **Date:** 2026-08-05
- **Arising from:** [2026-08-05 architecture review](../review/2026-08-05-architecture-review.md), F7 and F8/D1

## Context

At the time of review, the system already had the right instrument: a run-local ledger recording every gateway call
against a named actor. But three published behavioural facts are taken from the model's own output
instead of from the ledger:

- `delegated_to_specialist` is a field the income analyst *declares* in its JSON, not an
  observation that CrewAI's delegation tool fired.
- `rounds_used` and `sufficient` are fields the research agent declares.
- The specialist's actor is never set in the crew path, so any tool call it makes is ledgered
  against the analyst.

The consequence is that `metric_handoff_correctness` scores a self-report, and the published eval
headline shows **Handoff correctness 100.0%** from a `--judgment stub` run where the "handoff" is
an `if` statement. The report's warning banner flags decision accuracy as circular but does not
flag this row.

An agent that always claims it delegated scores identically to one that does. That is the exact
failure the two-directional check was designed to prevent, reintroduced one layer up.

## Decision

Any behavioural fact that appears in an eval metric, an emitted event, or a report must be derived
from the ledger or from Flow state — never from a model-declared field. Model-declared fields may
be retained as *claims*, and where a claim and an observation disagree, the disagreement is itself
recorded as a finding.

Specifically:

1. Ledger the delegation. `set_actor("self_employed_specialist_agent")` before the specialist's
   tools become reachable, and derive `delegated_to_specialist` from whether a call was recorded
   against that actor. Fixes defect D1 at the same time.
2. Derive `rounds_used` / `sufficient` from the deterministic loop
   ([ADR-0009](0009-deterministic-rag-plus-entailment-judge.md) makes this automatic).
3. Add the caveat to the eval report's warning banner for every metric whose stub value is
   tautological, not only decision accuracy.

## Implementation

The handoff remediation is implemented. In the demo roster, the specialist calculator wrapper
sets the specialist actor before its gateway call. `CrewJudgment.analyze_income()` compares the
model's declaration with that ledger observation, uses the observation as the state value scored
by the evaluator, and emits a `behavior.mismatch` finding if they disagree. The production roster
has no agent-to-agent income handoff, so the evaluator explicitly reports handoff correctness as
not scored for it (and for the deterministic stub).

Production research uses the deterministic retrieval loop, which supplies its round and
sufficiency values. The retained agent-driven demo research roster still carries its declared
`rounds_used` and `sufficient` fields as claims-only diagnostics. The evaluator excludes those
claims from retrieval metrics and reports demo retrieval as not scored; production retains the
deterministic loop's observed values.

## Consequences

- `metric_handoff_correctness` scores only the observed specialist-attributed calculator call for
  demo crew runs; it is not printed as a score for the production or stub rosters.
- A disagreement between the model declaration and observed call is retained as a behavior finding
  rather than being silently normalized.
- Schema fields become claims-under-verification. Keep them; a model claiming it delegated when the
  ledger says otherwise is a genuinely useful signal.

## Evidence

- `crews/verify/crew.py` sets `self_employed_specialist_agent` immediately before the specialist
  calculator gateway call.
- `crews/__init__.py` compares `delegated_to_specialist` with the ledger, records
  `handoff_claim_mismatch`, emits `behavior.mismatch`, and returns the observed value.
- `evals/run_evals.py` calculates the handoff metric from that observed state and prints it only
  for a demo crew run; other rosters are explicitly labelled not scored.
- `judgment.py` supplies production retrieval findings from the deterministic retrieval loop.

## Alternatives considered

**Trust the field and validate it occasionally.** Rejected — an unreliable instrument sampled
occasionally is an unreliable instrument.
