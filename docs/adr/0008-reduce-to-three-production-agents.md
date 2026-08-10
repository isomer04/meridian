# 0008 — Reduce to three production agents

- **Status:** Accepted
- **Date:** 2026-08-05
- **Arising from:** [2026-08-05 architecture review](../review/2026-08-05-architecture-review.md), F4

## Context

`docs/architecture.md` states the decomposition rule: an agent earns its existence by having its
own judgment, its own tool set, and its own failure mode. Anything fully determined by its inputs
is a function.

Applied strictly, four of the current eight do not clear the bar. In three cases the codebase
itself supplies the evidence — the agent is given no tools, is forbidden to compute, or has a
deterministic counterpart that is not visibly worse.

This ADR does not say the eight are *wrong*. They are a coherent demonstration of eight distinct
mechanisms. It says that a production deployment should not carry four LLM calls whose outputs are
determined by their inputs.

## Decision

The production roster is three agents:

1. **`collateral_agent`** — exercise or decline an offer of value acceptance, naming the factors
   weighed. Read-only tools: `search_guidelines`, `calc_ltv`. Never `order_appraisal`
   ([ADR-0012](0012-irreversible-tools-never-in-agent-menus.md)).
2. **`underwriter_agent`** — reconcile AUS findings against overlays, decide, grant or refuse the
   documented exception, write conditions and rationale. Absorbs the credit-interpretation and
   income-interpretation narrative work. No tools; decides on typed facts supplied by the Flow.
3. **`compliance_qc_agent`** — prohibited-basis reading, adverse-action specificity, and citation
   entailment ([ADR-0009](0009-deterministic-rag-plus-entailment-judge.md)). Runs *after* the
   deterministic citation check, never instead of it.

The following become deterministic workflow steps:

| Retired agent | Becomes | Justification |
|---|---|---|
| `intake_agent` | a validation function | Zero tools; six boolean presence checks. Keep the `ssn_on_file` rule, which was the one real insight and is already encoded. |
| `credit_liability_agent` | context passed to the underwriter | Zero tools; receives computed ratios; forbidden to recompute. |
| `self_employed_specialist_agent` | a direct `calc_self_employed_income` call | One tool; prompt says "you do not compute"; the trend judgment it claims is a Python comparison. |
| `guideline_research_agent` | deterministic `research_loop` | [ADR-0009](0009-deterministic-rag-plus-entailment-judge.md) |

## Consequences

- **Lost:** the delegation demonstration (`income_analyst_agent` → `self_employed_specialist_agent`),
  which is currently the system's only genuine agent-to-agent handoff. This is a real loss for the
  portfolio narrative and no loss at all for production. Keep the eight-agent configuration on a
  clearly-labelled branch or behind a `--roster demo` flag rather than deleting it.
- **Retained for the demo roster:** `handoff_correctness`. It is measured from the
  specialist-attributed calculator call in the ledger rather than a self-report; production
  correctly reports it as not scored because it has no agent handoff
  ([ADR-0011](0011-observed-telemetry-not-self-report.md)).
- **Gained:** four fewer LLM calls per file — the dominant cost and latency term — and four fewer
  places a model can produce a malformed enum that needs recovering (`crews/__init__.py:89-94`,
  `:200-204`).
- **Gained:** `intake` becomes deterministic, which matters because its failure mode is severe:
  wrongly declaring an application complete starts the 3-business-day LE clock.
- The `Judgment` protocol shrinks from seven methods to four (`collateral_decision`, `underwrite`,
  `qc_review`, plus `research` retained as deterministic-only).

## Evidence

- `intake/crew.py:29` — `tools=[]`; `judgment.py:119-134` — the six-boolean stub;
  `judgment.py:127-130` — the `ssn_on_file` rule, promoted from a live agent's reading.
- `verify/crew.py:104` — `tools=[]`; `verify/config/agents.yaml:78-81` — "you do not recompute
  them and you do not adjust them".
- `verify/crew.py:89` — the specialist's single tool; `verify/config/agents.yaml:55-56` — "you do
  not compute"; `calc/income.py:139-143` — the declining-trend rule it claims as its judgment.
- `verify/config/agents.yaml:86-114` and `judgment.py:262-357` — the collateral decision, where the
  stub is visibly an impoverished version of the real judgment. This is the contrast that
  justifies keeping the agent.
- `docs/architecture.md` — the decomposition rule this ADR applies.

## Alternatives considered

**Keep eight, gate four behind a flag by default-off.** Half-measure: the four would rot, and a
roster nobody runs is worse than a roster that does not exist.

**Reduce to two by folding QC into the underwriter.** Rejected — a control evaluated by the same
model that produced the artifact is not a control.
