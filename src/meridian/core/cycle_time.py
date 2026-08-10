"""Cycle-time model.

The panel shows a **modeled** cycle time, not a measured one, and the code says so.
Each step carries a documented `baseline_queue_days` and `baseline_touch_minutes`
beside the agent's **actually measured** wall-clock.

Scope, precisely: **application-complete (the 6 pieces of information) → conditional
approval.** Not close-to-fund.

Every baseline here is labelled `illustrative, unsourced` in docs/assumptions.md. "I
estimated this, here's my logic" survives scrutiny; a fabricated citation ends the
interview.

The irreducible floor matters as much as the reduction — it pre-empts "so why not one
day?". Three constraints are in it and are modeled as steps with `compressible=False`:
appraisal turn time, the LE compliance review, and VOE response.

Two more are named in docs/assumptions.md and are deliberately **not** in the floor, because
saying so is the point: borrower document return sits inside `verify_income`, which this
model treats as compressible and therefore optimistically; and title is out of scope
entirely, since the scope ends at conditional approval rather than closing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Baseline:
    step: str
    baseline_queue_days: float
    baseline_touch_minutes: float
    compressible: bool
    note: str = ""


# illustrative, unsourced — see docs/assumptions.md
BASELINES: dict[str, Baseline] = {
    "intake": Baseline("intake", 2.0, 45, True, "processor keys the file, chases the 6 pieces"),
    # A *deadline*, not a waiting period: §1026.19(e)(1)(iii) requires the LE to go out
    # within 3 business days, and this flow delivers immediately. The queue day is the
    # lender's compliance review of the disclosure, which is what does not compress.
    "disclose_le": Baseline("disclose_le", 1.0, 15, False, "LE compliance review; 3-day rule is a delivery deadline, not a wait"),
    "pull_credit": Baseline("pull_credit", 0.5, 10, True, "queue is a person, not the bureau"),
    "verify_income": Baseline("verify_income", 4.0, 90, True, "the single biggest touch sink"),
    "verify_employment": Baseline("verify_employment", 3.0, 20, False, "employer response time"),
    "submit_to_aus": Baseline("submit_to_aus", 1.0, 25, True, "DU submission + reading findings"),
    "order_appraisal": Baseline("order_appraisal", 9.0, 15, False, "AMC turn time; does not compress"),
    "guideline_research": Baseline("guideline_research", 3.0, 75, True, "overlay reconciliation"),
    "underwrite": Baseline("underwrite", 5.0, 120, True, "queue for an underwriter, then the review"),
    "compliance_qc": Baseline("compliance_qc", 1.5, 40, True, "pre-decision QC review"),
    "approval_wait": Baseline("approval_wait", 1.0, 10, False, "named human approval queue; modeled"),
}

# Underwriter touches on a clean conventional purchase file, before/after.
# Lead with touches; days is the derived number.
BASELINE_TOUCHES = 14
TARGET_TOUCHES = 2


@dataclass
class CycleTimeModel:
    steps_run: list[str] = field(default_factory=list)
    measured_seconds: dict[str, float] = field(default_factory=dict)
    value_acceptance_used: bool = False

    def record(self, step: str, seconds: float) -> None:
        if step not in self.steps_run:
            self.steps_run.append(step)
        self.measured_seconds[step] = self.measured_seconds.get(step, 0.0) + seconds

    def _applicable(self) -> list[Baseline]:
        """The steps this run is actually charged for.

        One place, because the value-acceptance exclusion has to hold in every number that
        quotes it. It used to be written out in `baseline_days` and `irreducible_floor_days`
        and *not* in `baseline_touch_minutes` or `does_not_compress`, so a waived appraisal
        showed a 9-day saving while still billing its 15 baseline touch minutes and still
        listing `order_appraisal` as non-compressible work that had been done.
        """
        out = []
        for step in self.steps_run:
            b = BASELINES.get(step)
            if not b:
                continue
            if step == "order_appraisal" and self.value_acceptance_used:
                continue
            out.append(b)
        return out

    def baseline_days(self) -> float:
        return round(sum(b.baseline_queue_days for b in self._applicable()), 1)

    def baseline_touch_minutes(self) -> float:
        return round(sum(b.baseline_touch_minutes for b in self._applicable()), 1)

    def irreducible_floor_days(self) -> float:
        """What the agents cannot compress: third-party turn time and regulatory
        waiting periods. Naming this is more credible than the reduction."""
        return round(
            sum(b.baseline_queue_days for b in self._applicable() if not b.compressible), 1
        )

    def modeled_days(self) -> float:
        """Compressible queue collapses to the agent's measured wall-clock; the
        floor stays. Rounded up to a half day because a lender's day is a business
        day, not a wall-clock day."""
        floor = self.irreducible_floor_days()
        agent_days = sum(self.measured_seconds.values()) / 86400.0
        modeled = floor + agent_days
        # Ceiling, not nearest. `round()` sent 9.02 modeled days down to 9.0 and reported a
        # cycle time shorter than the irreducible floor it is built on — rounding a
        # headline number in the flattering direction is exactly the thing not to do.
        return max(0.5, math.ceil(modeled * 2 - 1e-9) / 2)

    def summary(self) -> dict[str, Any]:
        return {
            "scope": "application-complete → conditional approval",
            "basis": "modeled, not measured — baselines are illustrative and unsourced",
            "baseline_days": self.baseline_days(),
            "modeled_days": self.modeled_days(),
            "irreducible_floor_days": self.irreducible_floor_days(),
            "baseline_touch_minutes": self.baseline_touch_minutes(),
            "baseline_underwriter_touches": BASELINE_TOUCHES,
            "modeled_underwriter_touches": TARGET_TOUCHES,
            "measured_agent_seconds": round(sum(self.measured_seconds.values()), 2),
            "does_not_compress": [b.step for b in self._applicable() if not b.compressible],
        }
