# 0009 — Deterministic RAG plus an entailment judge, not an agentic retriever

- **Status:** Accepted
- **Date:** 2026-08-05
- **Arising from:** [2026-08-05 architecture review](../review/2026-08-05-architecture-review.md), F5

## Context

`guideline_research_agent` drives a route → search → critique → requery loop with
`search_guidelines` and `critique_retrieval` as tools. The claim is that this is agentic RAG
rather than top-k stuffing.

The claim is accurate about the *mechanism* and wrong about the *need*, for four reasons:

1. **Corpus size.** `data/guidelines/` is 392 lines across three files, roughly 52 addressable
   sections. `docs/limitations.md` states these are hand-written paraphrases and that the offline
   embedder is a lexical character-n-gram hash.
2. **Query planning is already deterministic.** `_research_questions()` generates the entire
   question set from typed state, including an always-asked baseline tier. Its docstring records
   that the conditional-only version was tried and failed, because passing an overlay is a finding
   and a finding needs a citation. The most agentic part of agentic RAG has already, correctly,
   been taken from the model.
3. **Reformulation has a covering deterministic implementation.** `REFORMULATIONS` is a ten-entry
   regex table over the closed question space `_research_questions()` can produce.
4. **The agent's telemetry is already partly distrusted.** `corpora_routed` was replaced with a
   deterministic `route()` call after it misreported; `rounds_used` and `sufficient` remain
   self-reported and are emitted as instrumentation.

The controls that make the research agent safe — the citation guardrail and `verify_citation` —
are deterministic, and are the same controls that make it redundant.

## Decision

Retrieval is deterministic in production: `research_loop` with deterministic routing,
reformulation, and critique, capped at three rounds. `guideline_research_agent` is retired.

The LLM budget freed is redirected to the retrieval problem that actually needs a model: an
**entailment judge** over (retrieved chunk, asserted claim), run inside `compliance_qc_agent`.
This closes the gap the code already names honestly — the lexical checker cannot catch a claim
that inverts a section's logic while reusing its vocabulary. "LTV uses the *greater* of price or
appraised value" cited to `fannie:B2-1.2-01` passes today, because every term and number matches
and only the logic is reversed.

Ordering is fixed: existence check → lexical support check → entailment judge. The judge may only
*downgrade* a citation, never upgrade one the deterministic layer rejected.

## Consequences

- Retrieval becomes reproducible run-to-run, which makes the retrieval eval metric meaningful
  rather than variance-dominated.
- `total_rounds` and `all_sufficient` become observed facts rather than model claims.
- The citation-validity metric gains a real ceiling raise; `docs/limitations.md` and the eval
  report must be updated to describe the new ceiling rather than deleting the caveat.
- **Revisit trigger:** if the corpus grows past roughly 500 sections, or if genuine Selling Guide
  text replaces the paraphrases, re-open this decision. Agentic retrieval earns its place when the
  question space is open; here it is closed by `_research_questions()`.

## Evidence

- `data/guidelines/` — 392 lines, ~52 sections, three corpora.
- `flow.py:545-602` — deterministic question generation; `:552-561` records why the conditional-only
  version failed.
- `rag/tools.py:36-41` — deterministic routing, overlays never optional.
- `rag/tools.py:63-74` — the reformulation table; `:134-196` — the loop; `:172` — the three-round cap.
- `judgment.py:361-388` — the deterministic research implementation that already exists and would
  become the only one.
- `crews/__init__.py:158-160` — the corpus-routing misreport that forced deterministic replacement.
- `schemas.py:74-75` — `sufficient` and `rounds_used` as model-declared fields.
- `rag/tools.py:262-268` — the entailment gap, stated in the code.
- `crews/guardrails.py:16-50`, `rag/tools.py:247-323` — the deterministic controls.

## Alternatives considered

**Keep the agent and add the entailment judge.** Rejected: pays twice for retrieval and leaves the
self-reported round counts in place.

**Drop the critique loop entirely (single-shot top-k).** Rejected — the critique step is cheap,
deterministic, and catches the real failure where no overlay chunk was retrieved, which cannot be
sufficient because overlays bind.
