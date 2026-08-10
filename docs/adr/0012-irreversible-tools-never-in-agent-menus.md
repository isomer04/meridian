# 0012 — Irreversible tools are never in an agent tool menu

- **Status:** Accepted
- **Date:** 2026-08-05
- **Arising from:** [2026-08-05 architecture review](../review/2026-08-05-architecture-review.md), F8/D2

## Context

The architecture's stated rule is that a model may never choose a number or an irreversible side
effect. The Flow honours this: `pull_credit`, `order_appraisal` and `lock_rate` are invoked as
saga steps, and the collateral agent is handed `search_guidelines` and `calc_ltv` only.

But `tools_for_collateral()` in `tools.py` returns a menu containing `order_appraisal` and
`receive_appraisal`. It is unused — `VerifyCrew.run_collateral` builds its own menu — so the rule
currently holds *by accident*. A helper named `tools_for_collateral` sitting next to the
collateral crew is exactly the thing a future contributor wires up in good faith.

The gateway would still apply the Reg Z gate, so this is not a live compliance hole. It is a
latent one: post-disclosure, the gate is satisfied and a $600 irreversible order becomes reachable
by an agent's tool choice.

## Decision

1. Delete `tools_for_collateral()`.
2. Introduce an explicit `IRREVERSIBLE` set naming the tools no agent menu may contain:
   `pull_tri_merge`, `order_appraisal`, `lock_rate`. (`receive_appraisal` is a read, but ordering
   discipline is clearer if the whole AMC write path stays Flow-owned.)
3. Assert it at menu-construction time in `as_tool` / `_menu`, so a violation fails loudly where it
   is obvious rather than at an agent's first tool call.
4. Add a test asserting no constructed agent's tool list intersects `IRREVERSIBLE`.

The invariant, stated once: **the model chooses freely from the menu it is handed and nothing
beyond it; the Flow owns the path; and the model may never choose a number or an irreversible side
effect.** That sentence is already in `tools.py`'s docstring. This ADR makes it enforced rather
than aspirational.

## Consequences

- The rule stops depending on nobody wiring up an unused function.
- The check is structural, so a new crew added later inherits it.
- `tools_for_research()` and `tools_for_qc()` are also currently unused — the crews build their own
  menus inline. Either route all menu construction through these helpers or delete them too;
  keeping two ways to build a menu is how the invariant drifts.

## Evidence

- `tools.py:330-336` — `tools_for_collateral()`, returning `order_appraisal` and
  `receive_appraisal`.
- `verify/crew.py:123-129` — what the collateral agent is actually handed: `search_guidelines`,
  `calc_ltv`.
- `tools.py:339-347` — `tools_for_research()`, `tools_for_qc()`, likewise unused;
  `underwrite/crew.py:34-49` and `governance/crew.py:42` build their menus inline.
- `tools.py:20-22` — the invariant, currently a docstring.
- `tools.py:243-247` — the Reg Z gate that makes this latent rather than live.
- `flow.py:124-128` — `_order_appraisal`, the Flow-owned path that should remain the only one.

## Alternatives considered

**Leave it; it's unused.** Rejected — "unused" is a property of today's call graph, and the failure
mode if it is used is a $600 irreversible order chosen by a model.
