# Architecture decision records

One decision per file. Each records the context that forced the decision, the decision itself,
the consequences accepted, and the code that evidences it. An ADR is never edited to change its
decision — it is superseded by a later one.

**Format:** [MADR](https://adr.github.io/madr/)-flavoured, trimmed. Status is one of
`Accepted`, `Proposed`, `Superseded by NNNN`, `Rejected`.

`0001`–`0007` are **retroactive**: they record decisions already implemented in the codebase, so
that the reasoning survives independently of the person who made it. `0008`–`0012` arose from
the [2026-08-05 architecture review](../review/2026-08-05-architecture-review.md) and were accepted
through the sequenced [remediation plan](../plan/remediation-plan.md).

| # | Decision | Status |
|---|---|---|
| [0001](0001-deterministic-flow-owns-control-flow.md) | A deterministic Flow owns control flow; models never do | Accepted |
| [0002](0002-no-hands-off-autonomous-planner.md) | No hands-off autonomous planner | Accepted |
| [0003](0003-multi-agent-claim-scoped-to-roles.md) | The multi-agent claim is scoped to roles, not to interaction | Accepted |
| [0004](0004-tool-gateway-is-the-only-side-effect-path.md) | Agent and Flow tool calls share one gateway | Accepted |
| [0005](0005-saga-compensation-is-not-rollback.md) | Saga compensation reports what it could not undo | Accepted |
| [0006](0006-compliance-controls-in-code-not-prompts.md) | Compliance preconditions are code, not prompt instructions | Accepted |
| [0007](0007-judgment-seam-two-implementations.md) | One judgment protocol, two implementations | Accepted |
| [0008](0008-reduce-to-three-production-agents.md) | Reduce to three production agents | Accepted |
| [0009](0009-deterministic-rag-plus-entailment-judge.md) | Deterministic RAG plus an entailment judge, not an agentic retriever | Accepted |
| [0010](0010-human-approval-gates.md) | Four human approval gates | Accepted |
| [0011](0011-observed-telemetry-not-self-report.md) | Agent behaviour is observed, never self-reported | Accepted — handoff remediation implemented |
| [0012](0012-irreversible-tools-never-in-agent-menus.md) | Irreversible tools are never in an agent tool menu | Accepted |

## Writing a new one

Copy [`_template.md`](_template.md), take the next free number, add a row above. Keep it to one
page. If a section has nothing in it, delete the section rather than writing "N/A".

← [Back to README](../../README.md)
