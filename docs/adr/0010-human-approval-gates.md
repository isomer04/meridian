# 0010 — Four human approval gates

- **Status:** Accepted
- **Date:** 2026-08-05
- **Arising from:** [2026-08-05 architecture review](../review/2026-08-05-architecture-review.md), F6

## Context

`src/` contains no approval, review-queue, or hold construct anywhere. `governance()` writes a
decision and finishes. `adverse_action_and_unwind()` issues an ECOA notice to a borrower with no
sign-off. QC failure blocks silently and terminally, with no path to a person.

This is the largest architectural gap in the system — larger than the agent count. A system whose
entire thesis rests on the stakes of the domain currently has no human in it.

The README's headline claim is "underwriter touches drop from roughly 14 to 2". Two is not zero,
and the architecture should show where the two are.

## Decision

Four gates. Each pauses the Flow at a step boundary, persists a status a person can queue on, and
resumes on an explicit decision recorded against a named approver.

| # | Gate | Trigger | Why a human |
|---|---|---|---|
| G1 | **Denial** | before `adverse_action_and_unwind` writes the notice | An ECOA notice is an externally-visible legal artifact with a 30-day clock |
| G2 | **Overlay exception grant** | the OV-DTI-02 path in `underwrite` | An exception is by definition a decision to depart from policy; OV-GEN-02 already requires underwriter rationale in the file |
| G3 | **Declining an offered value acceptance** | `route_collateral` returns `appraisal_required` *while an offer was on the table* | It imposes $600 and ~9 days on the borrower. A dollar-threshold gate on `order_appraisal` is the cheaper equivalent |
| G4 | **QC failure** | `qc_passed is False` | Currently terminal and silent; belongs in a human queue with the findings attached |

Gates are implemented as Flow steps, not as agent behaviour. An agent may never decide that a gate
does not apply.

## Consequences

- The Flow becomes genuinely long-running: a gate can be open for hours. This is what the existing
  persistence and resume machinery was built for, so the cost is lower here than it would be
  elsewhere — `_persist` already writes authoritative state at every step boundary and
  `saga.restore()` already rebuilds from the ledger.
- Cycle-time claims must be restated. The current model records agent seconds; approval wait is a
  new, and probably dominant, term. `docs/assumptions.md` must carry a baseline for it, labelled
  modeled like the rest.
- Demo scenarios need an auto-approve mode so `run_cli.py` stays a 90-second walkthrough. That mode
  must be visibly named in output — a silently auto-approving gate is worse than no gate.
- Two gates (G1, G4) sit on the denial path, which is the path that already has the most machinery.
  Sequence them after QC and before the notice write, preserving the existing ordering rationale.

## Evidence

- `flow.py:620-642` — `governance()`; no approval construct.
- `flow.py:644-685` — `adverse_action_and_unwind()`; notice written directly to `notices`.
- `flow.py:628-640` — QC failure calls `_finish("qc_blocked")` and returns.
- `judgment.py:521-534` — the exception grant that G2 covers.
- `flow.py:132-147` — `_persist`, the step-boundary write the gates will rely on.
- `flow.py:177-185` — `saga.restore()`, the resume path.
- A repository-wide search of `src/` for human/approval/HITL constructs returns nothing.

## Alternatives considered

**Approval as a post-hoc review queue** (the file completes, a human reviews later). Rejected for
G1 — once the notice is sent, review is archaeology.

**Approval implemented as an agent that "asks".** Rejected — a gate a model can route around is not
a gate. Same reasoning as [ADR-0006](0006-compliance-controls-in-code-not-prompts.md).
