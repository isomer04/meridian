# Roster comparison

The comparison runner is implemented, but a new live result is not committed from this branch
because `OPENAI_API_KEY` was not available and the new entailment task invalidates the historical
text cassettes. No model output or cost was fabricated.

Run the controlled comparison over the identical golden set with visibly auto-approved gates:

```bash
uv run python evals/compare_rosters.py --mode live
```

The generated table reports decision accuracy, errors, wall time, model cost, and case-by-case
stub/production/demo decisions. Any disagreement is shown beside the golden label, which is the
referee defined by ADR-0007.
