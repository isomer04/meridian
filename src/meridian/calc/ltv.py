"""LTV / CLTV.

The rule that catches people out: on a purchase, LTV is measured against the **lesser
of purchase price or appraised value**. An appraisal above the contract price does not
create equity, which is why scenario 3 — appraisal comes in low, LTV breaches, file
denies — is a *real* failure mode and not a contrived one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _r(x: float) -> float:
    return round(x + 1e-9, 4)


@dataclass
class LTVResult:
    ltv: float
    cltv: float
    basis: float
    basis_reason: str
    mi_required: bool
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ltv": round(self.ltv, 2),
            "cltv": round(self.cltv, 2),
            "value_basis": self.basis,
            "value_basis_reason": self.basis_reason,
            "mi_required": self.mi_required,
            "notes": self.notes,
        }


def calc_ltv(
    loan_amount: float,
    purchase_price: float | None = None,
    appraised_value: float | None = None,
    subordinate_liens: float = 0.0,
    transaction_type: str = "purchase",
) -> LTVResult:
    notes: list[str] = []

    if transaction_type == "purchase":
        # `is not None`, not truthiness: a supplied zero is a *bad* value, not a missing
        # one, and it has to reach the positivity check below rather than being silently
        # dropped so the other figure becomes the basis.
        candidates = [v for v in (purchase_price, appraised_value) if v is not None]
        if not candidates:
            raise ValueError("purchase LTV needs a price or a value")
        basis = min(candidates)
        if basis <= 0:
            raise ValueError("purchase LTV needs a positive price or value")
        if purchase_price is not None and appraised_value is not None:
            if appraised_value < purchase_price:
                basis_reason = (
                    f"appraised value {appraised_value:,.0f} is below contract price "
                    f"{purchase_price:,.0f} — LTV measured against the lesser"
                )
                notes.append(
                    "value shortfall of "
                    f"{purchase_price - appraised_value:,.0f}; borrower must cover the gap in cash "
                    "or the loan amount must come down"
                )
            else:
                basis_reason = (
                    f"appraised value {appraised_value:,.0f} at or above contract price "
                    f"{purchase_price:,.0f} — LTV measured against price; the surplus is not equity"
                )
        else:
            basis_reason = "single value available"
    else:
        if appraised_value is None:
            raise ValueError("refinance LTV needs an appraised value")
        if appraised_value <= 0:
            raise ValueError("refinance LTV needs a positive appraised value")
        basis = appraised_value
        basis_reason = "refinance — appraised value is the basis"

    ltv = _r(loan_amount / basis * 100)
    cltv = _r((loan_amount + subordinate_liens) / basis * 100)
    mi_required = ltv > 80.0
    if mi_required:
        notes.append(f"LTV {ltv:.2f}% exceeds 80% — mortgage insurance required")
    return LTVResult(ltv, cltv, _r(basis), basis_reason, mi_required, notes)


def max_loan_for_ltv(basis_value: float, max_ltv_pct: float) -> float:
    """Used when a low appraisal forces the loan amount down; the counter-offer
    figure a processor would actually quote."""
    return float(int(basis_value * max_ltv_pct / 100.0))
