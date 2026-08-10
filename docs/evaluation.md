# Evaluation

The question is *"how do you know it works?"*, and testing a non-deterministic system is the
hard problem of the field.

```bash
uv run python evals/run_evals.py        # → evals/report.md
uv run python evals/run_evals.py --judgment crew --roster production --mode live
uv run python evals/run_evals.py --judgment crew --roster demo --mode live
```

Ten golden-set files — the five demo scenarios plus five variants covering the boundaries the
demos don't: an overlay with no exception path, an exception path whose conditions are *not*
met, a free option that should be declined, and a file that is not legally an application.

Each evaluation case resets `meridian.core.db` to an isolated database in the evaluation work
directory. Evaluations therefore do not read or write the application's `meridian.db`.

The report has five headline measures, plus cost/latency and retrieval diagnostics.

| Metric | What it measures |
|---|---|
| **Decision accuracy** | final decision vs. the expected label |
| **Citation validity** | existence → lexical support → LLM entailment under a crew roster; each layer can only downgrade |
| **Handoff correctness** | demo roster only: did the specialist-attributed calculator call actually appear in the ledger? |
| **Tool-call precision / recall** | conditional tool calling, quantified. A W-2 file must never reach `calc_self_employed_income` |
| **Run-to-run variance** | same file × N runs → how often the same decision |

Cost and latency are aggregated per agent across evaluated runs, with totals and mean latency;
retrieval diagnostics report rounds, sufficiency, and cap adherence when observed. "Which agent
is expensive?" is a real operating question.

> **Read the report's caveat first.** Under `--judgment stub`, decision accuracy and handoff
> correctness are tautological. Handoff is therefore suppressed, not printed as a result. Stub
> citation validity exercises the deterministic existence/lexical floor only; crew runs add the
> downgrade-only entailment judge. Eval flows use visibly recorded `AUTO-APPROVE` decisions so
> approvals cannot silently disappear from the measurement context.

The golden citation challenges include a supported lesser-of claim and its vocabulary-preserving
logical inversion ("greater of"). The lexical layer accepts both; the semantic QC task must reject
the inversion. Retrieval round counts and sufficiency come from the deterministic loop, not model
self-report.

For the required three-way comparison, run `uv run python evals/compare_rosters.py --mode live`.
It publishes [roster-comparison.md](../evals/roster-comparison.md) with case-level decisions,
accuracy, errors, cost, and latency for `stub`, `production`, and `demo` over the identical set.

← [Back to README](../README.md)
