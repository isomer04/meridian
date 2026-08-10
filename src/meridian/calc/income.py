"""Qualifying income. Pure functions, no model in the loop.

This module is the concrete answer to "why is this an agent and not a function?".
Income *calculation* is deterministic given the documents, so it is a function. Income
*analysis* — deciding which method applies, whether a gap is explainable, whether the
file needs a self-employed specialist — is judgment, so that is an agent.

The agent interprets. It never computes.

Rounding: dollars-and-cents at each documented step, matching how a processor works a
1008 by hand. Not floats accumulated to 12 places.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _r(x: float) -> float:
    return round(x + 1e-9, 2)


@dataclass
class IncomeResult:
    monthly_qualifying: float
    method: str
    components: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "monthly_qualifying_income": self.monthly_qualifying,
            "method": self.method,
            "components": self.components,
            "notes": self.notes,
        }


def calc_w2_income(
    base_annual: float,
    year_history: list[dict[str, Any]] | None = None,
    bonus_history: list[float] | None = None,
    overtime_history: list[float] | None = None,
    employment_gap_months: float = 0.0,
) -> IncomeResult:
    """Salaried W-2 borrower.

    Base salary is taken at its current annualized rate — you do not average a
    salary, you average *variable* income. Bonus and overtime are averaged over the
    documented history (24 months preferred), and a variable-income stream that is
    declining year over year is taken at the lower figure rather than the average.

    An employment gap over 6 months within the last two years drops variable income
    entirely and flags the file: the history is no longer a reliable predictor.
    """
    notes: list[str] = []
    components: dict[str, float] = {"base_monthly": _r(base_annual / 12.0)}

    gap_disqualifies_variable = employment_gap_months > 6
    if employment_gap_months > 0:
        notes.append(
            f"employment gap of {employment_gap_months:g} months documented"
            + ("; variable income excluded" if gap_disqualifies_variable else "")
        )

    def _variable(history: list[float] | None, label: str) -> float:
        if not history or gap_disqualifies_variable:
            return 0.0
        avg = sum(history) / len(history) / 12.0
        if len(history) >= 2 and history[-1] < history[-2]:
            declining = history[-1] / 12.0
            notes.append(
                f"{label} declining year over year — using most recent "
                f"({_r(declining)}/mo) rather than the {_r(avg)}/mo average"
            )
            return _r(declining)
        return _r(avg)

    components["bonus_monthly"] = _variable(bonus_history, "bonus")
    components["overtime_monthly"] = _variable(overtime_history, "overtime")

    if year_history:
        years = len(year_history)
        if years < 2:
            notes.append(f"only {years} year(s) of history documented — 2 years preferred")

    total = _r(sum(components.values()))
    return IncomeResult(total, "w2_salaried", components, notes)


def calc_self_employed_income(
    schedule_c_net: list[float] | None = None,
    k1_ordinary: list[float] | None = None,
    add_backs: dict[str, float] | None = None,
    deductions: dict[str, float] | None = None,
    years: int = 2,
) -> IncomeResult:
    """Schedule C / K-1 cash-flow analysis.

    Self-employed income is a *cash flow* exercise, not a line off a tax return.
    Start from net profit, add back non-cash deductions (depreciation, depletion,
    amortization, business use of home, the deductible half of meals), subtract items
    that are real cash out but not on the P&L, then average over the documented
    period.

    A declining two-year trend is taken at the most recent year, never the average —
    averaging a declining business overstates capacity, which is the specific error
    this analysis exists to avoid.
    """
    notes: list[str] = []
    # `years` is the documented averaging window, so it has to actually truncate the
    # inputs. Taking the most recent N years — the tail, not the head — because a longer
    # filing history is the common case and the recent years are the relevant ones.
    if years <= 0:
        raise ValueError("years must be positive")
    schedule_c_net = (schedule_c_net or [])[-years:]
    k1_ordinary = (k1_ordinary or [])[-years:]
    add_backs = add_backs or {}
    deductions = deductions or {}

    annual_by_year: list[float] = []
    n = max(len(schedule_c_net), len(k1_ordinary))
    for i in range(n):
        base = 0.0
        if i < len(schedule_c_net):
            base += schedule_c_net[i]
        if i < len(k1_ordinary):
            base += k1_ordinary[i]
        annual_by_year.append(base)

    total_add_backs = _r(sum(add_backs.values()))
    total_deductions = _r(sum(deductions.values()))
    adjusted = [_r(a + total_add_backs - total_deductions) for a in annual_by_year]

    if not adjusted:
        return IncomeResult(0.0, "self_employed_cash_flow", {}, ["no business returns provided"])

    declining = len(adjusted) >= 2 and adjusted[-1] < adjusted[-2]
    if declining:
        annual = adjusted[-1]
        notes.append(
            f"declining trend {_r(adjusted[-2])} → {_r(adjusted[-1])}: using most recent year, "
            "not the average"
        )
    else:
        annual = _r(sum(adjusted) / len(adjusted))

    if len(adjusted) < 2:
        notes.append("less than 2 years of business returns — a 5-year history is the usual ask")
    if add_backs:
        notes.append("add-backs: " + ", ".join(f"{k} {v:,.0f}" for k, v in add_backs.items()))
    if deductions:
        notes.append("deductions: " + ", ".join(f"{k} {v:,.0f}" for k, v in deductions.items()))

    # The per-year series is a *note*, not a component. It used to sit in `components`,
    # which is `dict[str, float]` and is summed downstream — a list in there is a type lie
    # that only stayed quiet because two `type: ignore`s were holding the door shut.
    notes.append(
        "adjusted annual by year: " + ", ".join(f"{a:,.2f}" for a in adjusted)
    )
    components = {
        "add_backs_annual": total_add_backs,
        "deductions_annual": total_deductions,
        "used_annual": annual,
    }
    return IncomeResult(_r(annual / 12.0), "self_employed_cash_flow", components, notes)


def calc_rental_income(gross_monthly_rent: float, occupancy_factor: float = 0.75) -> IncomeResult:
    """75% of gross rent is the standard vacancy/maintenance haircut."""
    net = _r(gross_monthly_rent * occupancy_factor)
    return IncomeResult(
        net,
        "rental_75pct",
        {"gross_monthly_rent": _r(gross_monthly_rent), "factor": occupancy_factor},
        [f"{int(occupancy_factor * 100)}% of gross rent per standard vacancy factor"],
    )
