"""Credit bureau — tri-merge.

A tri-merge pulls Equifax, Experian and TransUnion and the middle score is the
qualifying score (on a joint file, the lower of the two borrowers' middle scores).

This is the step with no compensation. Scoring models dedupe mortgage inquiries
inside a 14–45 day rate-shopping window, so a duplicate pull may not *cost the
borrower points* — but the inquiry still appears on the report, and it is still a
disclosure event. You cannot un-ring that bell, which is why the idempotency key for
this step is derived from the saga step rather than from anything a model produced.

Deliberately NOT decorated with `@requires_state(intent_to_proceed=True)`:
§1026.19(e)(2)(i)(B) carves out a bona fide credit-report fee from the fee
prohibition. `tests/test_core.py` asserts the absence of that decorator, so a
well-meaning future edit that "adds the missing gate" fails the suite.
"""

from __future__ import annotations

from typing import Any

from ..core.db import get_db
from ..core.errors import VendorError
from ..core.events import emit
from .fixtures import fixture_for


def pull_tri_merge(loan_id: str, borrower: dict[str, Any], **_: Any) -> dict[str, Any]:
    """Order a tri-merge. A hard inquiry, permanently, on all three reports."""
    fx = fixture_for(loan_id, "credit")
    if fx is None:
        raise VendorError("credit_bureau", f"no credit file on record for {loan_id}", retryable=False)
    if fx.get("_fail"):
        raise VendorError("credit_bureau", fx.get("_fail", "bureau timeout"))

    db = get_db()
    db.record_vendor_call(loan_id, "credit_bureau", "tri_merge")

    scores = fx["scores"]
    middle = sorted(scores.values())[1]
    result = {
        "vendor": "tri-merge (EFX/EXP/TU)",
        "scores": scores,
        "qualifying_score": middle,
        "qualifying_score_basis": "middle of three bureau scores",
        "inquiry": {
            "type": "hard",
            "permanent": True,
            "note": (
                "deduped for scoring inside the 14–45 day rate-shopping window, "
                "but the inquiry remains on the report"
            ),
        },
        "liabilities": fx["liabilities"],
        "derogatory": fx.get("derogatory", []),
        "housing_history_months": fx.get("housing_history_months", 24),
    }
    emit(
        "tool.result",
        actor="credit_bureau.pull_tri_merge",
        loan_id=loan_id,
        qualifying_score=middle,
        tradelines=len(fx["liabilities"]),
    )
    return result


def inquiry_count(loan_id: str | None = None) -> int:
    """Inquiries on the report — for *this* loan when one is named.

    Used by scenario 4 to show the counter reads 1 after a duplicate submission. It has to
    be per-loan or the demonstration is worthless: a shared database with two loans in it
    reports 2, and "the idempotency key held" stops being something the number can show.
    """
    return get_db().vendor_call_count("credit_bureau", "tri_merge", loan_id=loan_id)
