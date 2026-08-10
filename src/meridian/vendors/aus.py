"""Automated Underwriting System — Desktop Underwriter (Fannie Mae).

There must be an AUS step. Real conventional origination runs through **Desktop
Underwriter** or Freddie's **Loan Product Advisor**: you submit the file, you receive
findings, you work the conditions. A system with no AUS reads as designed by someone
who has never seen a live file.

Two consequences for the architecture, both good:

1. `underwriter_agent`'s real job becomes **reconciling DU findings against internal
   overlays** — which is what a human underwriter actually does — rather than
   inventing a decision from raw guidelines.
2. **Value acceptance** arrives here, as an offer in the findings.

On that second point: Fannie **eliminated the term "appraisal waiver"** from the
Selling Guide and replaced it with **value acceptance** (Freddie's equivalent is ACE).
It is *Fannie's offer, issued through DU* — a lender does not grant one from its own
LTV logic. So the flow is: findings arrive carrying an offer, and `collateral_agent`
decides whether to exercise it. Deriving a waiver from `LTV <= 80` would be backwards.

This is read-only, which is why its saga row needs no compensation.
"""

from __future__ import annotations

from typing import Any

from ..core.db import get_db
from ..core.errors import VendorError
from ..core.events import emit
from .fixtures import fixture_for


def submit_to_du(loan_id: str, casefile: dict[str, Any], **_: Any) -> dict[str, Any]:
    """Submit to DU and return findings.

    Findings carry: a recommendation, an eligibility verdict, verification messages,
    conditions, and — when offered — a value acceptance offer. Note that
    recommendation and eligibility are *separate*: Approve/Ineligible is a real and
    common combination, and conflating them is a domain error.
    """
    fx = fixture_for(loan_id, "aus")
    if fx is None:
        raise VendorError("du", f"no AUS fixture for {loan_id}", retryable=False)
    if fx.get("_fail"):
        raise VendorError("du", fx["_fail"])

    db = get_db()
    db.record_vendor_call(loan_id, "du", "submit")

    findings = {
        "vendor": "Desktop Underwriter",
        "casefile_id": fx.get("casefile_id", f"DU-{loan_id}"),
        "submission_number": db.vendor_call_count("du", "submit"),
        "recommendation": fx["recommendation"],  # Approve | Refer | Refer with Caution | Out of Scope
        "eligibility": fx["eligibility"],  # Eligible | Ineligible
        "verification_messages": fx.get("verification_messages", []),
        "conditions": fx.get("conditions", []),
        "value_acceptance": fx.get("value_acceptance"),
        "risk_factors": fx.get("risk_factors", []),
        "note": (
            "recommendation and eligibility are independent — Approve/Ineligible is a "
            "real combination and must not be collapsed"
        ),
    }
    emit(
        "tool.result",
        actor="aus.submit_to_du",
        loan_id=loan_id,
        recommendation=findings["recommendation"],
        eligibility=findings["eligibility"],
        value_acceptance=bool(findings["value_acceptance"]),
    )
    return findings


def value_acceptance_offer(findings: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the offer, if DU made one.

    Shape: {"offered": bool, "type": "value_acceptance", "conditions": [...]}
    An offer is an *option*, not an instruction — see `collateral_agent`.
    """
    va = findings.get("value_acceptance")
    if not va or not va.get("offered"):
        return None
    return va
