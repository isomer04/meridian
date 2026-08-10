# Agent rosters

Production uses three agents. The historical eight-agent configuration remains runnable as a
demonstration roster; it is not the production architecture.

```bash
uv run python run_cli.py --judgment crew --roster production --auto-approve
uv run python run_cli.py --judgment crew --roster demo --auto-approve
```

## Production roster

The rule for earning an agent is: **independent judgment, a distinct failure mode, and work that
cannot be fully determined from typed inputs.** Applying that rule leaves three roles.

| Agent | Judgment retained | Control boundary |
|---|---|---|
| `collateral_agent` | Whether to exercise an offered value acceptance | Cannot order an appraisal. Declining an offer triggers human gate G3 before the Flow places the order. |
| `underwriter_agent` | Reconcile DU findings with lender overlays and write the decision rationale/conditions | Receives computed income, credit context, DTI/LTV, and deterministic retrieval. An overlay exception triggers G2. |
| `compliance_qc_agent` | Prohibited-basis review, adverse-action specificity, and semantic citation entailment | Runs after deterministic citation existence and lexical checks. Its entailment verdict may only downgrade. QC failure triggers G4. |

The following production steps are deterministic:

| Former agent | Production step |
|---|---|
| `intake_agent` | Six-piece validation, including the `ssn_on_file` rule |
| `credit_liability_agent` | Credit interpretation context supplied to underwriting |
| `income_analyst_agent` + `self_employed_specialist_agent` | Typed dispatch to `calc_w2_income` and/or `calc_self_employed_income`; mixed files run both streams |
| `guideline_research_agent` | `research_loop`: deterministic routing, search, critique, and reformulation, capped at three rounds |

## Demonstration roster

`--roster demo` retains all eight roles across the four crew packages. It exists to demonstrate
conditional menus and an actual agent-to-agent handoff. The handoff metric is meaningful only
for this roster and is derived from a specialist-attributed tool call in the ledger; the model's
`delegated_to_specialist` field is retained only as a claim and disagreement becomes a finding.

| # | Crew | Agent | Demonstrates |
|---|---|---|---|
| 1 | Intake | `intake_agent` | Model-based six-piece/document classification |
| 2 | Verify | `income_analyst_agent` | Conditional tools and delegation |
| 3 | Verify | `self_employed_specialist_agent` | Observable handoff target |
| 4 | Verify | `credit_liability_agent` | Narrative credit interpretation without arithmetic |
| 5 | Verify | `collateral_agent` | Value-acceptance judgment |
| 6 | Underwrite | `guideline_research_agent` | Historical agent-driven retrieval loop |
| 7 | Underwrite | `underwriter_agent` | DU/overlay reconciliation |
| 8 | Governance | `compliance_qc_agent` | Citation, ECOA, and adverse-action control |

Prompts remain in `src/meridian/crews/*/config/{agents,tasks}.yaml`. Irreversible tools are
rejected by the shared agent-tool adapter, so neither roster can expose `pull_tri_merge`,
`order_appraisal`, or `lock_rate` to a model.

← [Back to README](../README.md) · [ADR-0008](adr/0008-reduce-to-three-production-agents.md)
