"""Debt-to-income. Deterministic.

The credit/liability agent orders the tri-merge and interprets what it finds. The
ratio itself is computed here, in Python, and the number the underwriter sees is this
number. That is the load-bearing distinction of the whole design: the model may
choose the path, it may never choose a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _r(x: float) -> float:
    return round(x + 1e-9, 2)


@dataclass
class DTIResult:
    front_end: float
    back_end: float
    piti: float
    monthly_debts: float
    monthly_income: float
    components: dict[str, float] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "front_end_ratio": self.front_end,
            "back_end_ratio": self.back_end,
            "piti": self.piti,
            "monthly_debts": self.monthly_debts,
            "monthly_qualifying_income": self.monthly_income,
            "components": self.components,
            "excluded_liabilities": self.excluded,
        }


def monthly_pi(principal: float, annual_rate_pct: float, term_years: int = 30) -> float:
    """Standard amortizing payment. A zero rate degrades to straight-line."""
    if term_years <= 0:
        raise ValueError("term_years must be positive to compute a payment")
    n = term_years * 12
    r = annual_rate_pct / 100.0 / 12.0
    if r == 0:
        return _r(principal / n)
    factor = (1 + r) ** n
    return _r(principal * r * factor / (factor - 1))


def calc_piti(
    loan_amount: float,
    annual_rate_pct: float,
    term_years: int = 30,
    annual_property_tax: float = 0.0,
    annual_hazard_insurance: float = 0.0,
    monthly_hoa: float = 0.0,
    monthly_mi: float = 0.0,
) -> dict[str, float]:
    """PITI plus MI and HOA — the full housing payment a DTI is measured against.

    HOA is included even though the acronym does not cover it; excluding it is a
    common and expensive mistake on condo files.
    """
    pi = monthly_pi(loan_amount, annual_rate_pct, term_years)
    taxes = _r(annual_property_tax / 12.0)
    hazard = _r(annual_hazard_insurance / 12.0)
    total = _r(pi + taxes + hazard + monthly_hoa + monthly_mi)
    return {
        "principal_and_interest": pi,
        "taxes": taxes,
        "hazard_insurance": hazard,
        "hoa": _r(monthly_hoa),
        "mortgage_insurance": _r(monthly_mi),
        "total_piti": total,
    }


# Liability types excluded from the back-end ratio, with the reason.
EXCLUSION_RULES = {
    "authorized_user": "authorized-user tradeline, not the borrower's obligation",
    "paid_by_others": "documented 12 months paid by another party",
    "deferred_student_no_payment": "deferred student loan — 0.5% of balance imputed instead",
    "under_10_months": "installment debt with fewer than 10 payments remaining",
}


def calc_dti(
    monthly_qualifying_income: float,
    piti: float,
    liabilities: list[dict[str, Any]] | None = None,
) -> DTIResult:
    """Front-end = housing / income. Back-end = (housing + recurring debt) / income.

    Revolving accounts use the greater of the stated minimum or 5% of the balance
    when no minimum is reported. Deferred student loans with no payment impute 0.5%
    of the balance rather than counting zero.
    """
    liabilities = liabilities or []
    if monthly_qualifying_income <= 0:
        raise ValueError("qualifying income must be positive to compute DTI")

    debts = 0.0
    components: dict[str, float] = {}
    excluded: list[str] = []

    for li in liabilities:
        kind = li.get("type", "installment")
        name = li.get("creditor", kind)
        payment = float(li.get("monthly_payment") or 0.0)
        balance = float(li.get("balance") or 0.0)

        if kind in EXCLUSION_RULES and kind != "deferred_student_no_payment":
            excluded.append(f"{name}: {EXCLUSION_RULES[kind]}")
            continue
        if kind == "deferred_student_no_payment":
            payment = _r(balance * 0.005)
            excluded.append(f"{name}: {EXCLUSION_RULES[kind]} (imputed {payment})")
        elif kind == "revolving" and payment == 0 and balance > 0:
            payment = _r(balance * 0.05)

        if payment <= 0:
            continue
        debts = _r(debts + payment)
        # Two cards from the same issuer are two obligations. Overwriting on the creditor
        # name left `components` summing to less than `monthly_debts`, so the breakdown a
        # reviewer checks the ratio against did not reconcile with the ratio.
        components[name] = _r(components.get(name, 0.0) + payment)

    front = _r(piti / monthly_qualifying_income * 100)
    back = _r((piti + debts) / monthly_qualifying_income * 100)
    return DTIResult(front, back, _r(piti), debts, _r(monthly_qualifying_income), components, excluded)
