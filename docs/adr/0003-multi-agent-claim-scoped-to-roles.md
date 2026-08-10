# 0003 — The multi-agent claim is scoped to roles, not to interaction

- **Status:** Accepted (recorded retroactively 2026-08-05)
- **Date:** 2026-08-05

## Context

"Multi-agent system" is used to mean two different things: several LLM-backed roles contributing
to one outcome, and several agents *talking to each other*. This system satisfies the first
comfortably and the second in exactly one place. Claiming the second without the qualifier is the
easiest thing in a walkthrough to get caught on.

## Decision

The system is described as multi-agent on the **role** axis: distinct roles, distinct tool sets,
distinct failure modes, distinct model tiers. Wherever the interaction axis is discussed, the
distinction between *agent-to-agent delegation* and *workflow-controlled sequencing* is stated
explicitly, and the single delegation site is named.

## Consequences

- Documentation must never describe the crews as "collaborating". They do not; the Flow sequences
  them and passes typed state between them.
- The one genuine handoff carries disproportionate explanatory weight, which makes its
  instrumentation important — see [ADR-0011](0011-observed-telemetry-not-self-report.md).

## Evidence

**Genuine agent-to-agent delegation — one site:**

- `verify/crew.py:79-99` — two agents, one task, assigned to the analyst only (`:91-92`). The
  specialist is reachable solely through CrewAI's delegation tool.
- `verify/config/agents.yaml:29` — `allow_delegation: true`, on the analyst alone.
- `tests/test_crews.py:223-228` — asserts it is the only delegator.

**Workflow-controlled sequencing — everywhere else:**

- `flow.py` calls `self.judgment.<method>()` at lines 200, 345, 391, 438, 522, 534, 624.
- Each constructs a fresh, isolated single-agent crew: `intake/crew.py:28-33`,
  `verify/crew.py:103-116`, `verify/crew.py:120-139`, `underwrite/crew.py:58-76`,
  `underwrite/crew.py:78-90`, `governance/crew.py:38-51`.
- No agent output reaches another agent as a message; it is typed, persisted, and re-rendered as a
  JSON input string.

**Role differentiation that supports the claim:**

- `_base.py:65-99` — per-agent model tier resolution from `agents.yaml`.
- `tools.py:314-327` — per-agent filtered tool menus.

## Alternatives considered

**Drop the multi-agent framing entirely and call it an LLM pipeline.** More precise about
mechanism, less precise about design: the roles genuinely have separate failure modes and separate
prompts, and collapsing them into "a pipeline" would lose that. The qualifier is the honest middle.
