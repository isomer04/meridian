# 0007 — One judgment protocol, two implementations

- **Status:** Accepted (recorded retroactively 2026-08-05)
- **Date:** 2026-08-05

## Context

When a multi-agent system produces a wrong outcome, the expensive question is *which layer was
wrong* — the orchestration or the judgment. Most such systems cannot answer it, and that is where
debugging time goes.

## Decision

`judgment.py` defines a protocol of seven methods. Two implementations satisfy it:

- `StubJudgment` — rules, no model. It evaluates the overlay policy **for real** against the same
  corpus the agents cite. It is explicitly *not* a lookup table of expected answers; if it were,
  the eval baseline it provides would be worthless.
- `CrewJudgment` — the LLM agents.

Both drive the identical Flow, routers, saga, tool gateway and policy controls. Only the judgment
changes.

Seven methods for eight agents, because the income analyst and its specialist share one entry
point — the handoff happens *inside* it, which is the point.

## Consequences

- **Failures localise.** Wrong under `crew` but right under `stub` → judgment. Wrong under both →
  orchestration.
- **The system runs with no API key.** A reviewer gets a working demo with real Flow, saga,
  controls, retrieval and citation verification.
- **Build order was risk order.** `core/`, `calc/`, `vendors/`, `rag/` have zero CrewAI imports.
- **The cost is real duplication**: the overlay policy is expressed twice. Worth it here because
  the rules are small and the diagnostic value is high. On a system whose judgment could not be
  expressed as rules, this trade would not pay.
- **A caveat that must be stated wherever stub numbers are published**: the stub's rules and the
  golden set's expected labels were authored by the same person, so stub decision accuracy largely
  measures self-consistency. The eval report says so; anything else quoting the numbers must too.

## Evidence

- `judgment.py:51-63` — the protocol.
- `judgment.py:104-782` — `StubJudgment`, evaluating overlays for real (see the DTI exception path
  at `:501-534`).
- `crews/__init__.py:53-339` — `CrewJudgment` satisfying the same protocol.
- `judgment.py:1-19` — the rationale, stated in the module docstring.
- `tests/test_crews.py:271-291` — crew and stub agreeing on a scenario decision.
- `evals/report.md` — the circularity warning banner.

## Alternatives considered

**A mock judgment returning canned answers.** Rejected — it would make every offline eval number
decorative, which is worse than having no offline path.
