"""Pricing / lock desk.

The compensating action here is the one people find surprising, so it is worth being
precise: releasing a lock does not restore the prior state. A relock is subject to
**worst-case pricing** — the lender takes the worse of the original and the current
market. The borrower is not made whole; they are put back in the market at a
disadvantage.

That is the real lesson of the saga pattern. A compensation is a business-level
apology, and some apologies cost money.
"""

from __future__ import annotations

from typing import Any

from ..core.db import get_db
from ..core.errors import VendorError
from ..core.events import emit
from .fixtures import fixture_for


def lock_rate(loan_id: str, loan_amount: float = 0.0, days: int = 45, **_: Any) -> dict[str, Any]:
    fx = fixture_for(loan_id, "pricing") or {}
    if fx.get("_fail"):
        raise VendorError("pricing", fx["_fail"])

    db = get_db()
    db.record_vendor_call(loan_id, "pricing", "lock")
    lock = {
        "vendor": "secondary/lock desk",
        "lock_id": fx.get("lock_id", f"LK-{loan_id}"),
        "rate": fx.get("rate", 6.5),
        "points": fx.get("points", 0.0),
        "lock_days": days,
        "status": "locked",
        "loan_amount": loan_amount,
        "current_market_rate": fx.get("current_market_rate", fx.get("rate", 6.5)),
    }
    emit("tool.result", actor="pricing.lock_rate", loan_id=loan_id, rate=lock["rate"], days=days)
    return lock


def release_lock(loan_id: str, forward_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compensating action — and a deliberately lossy one."""
    lock = forward_result or {}
    fx = fixture_for(loan_id, "pricing") or {}
    original = float(lock.get("rate", fx.get("rate", 6.5)))
    market = float(fx.get("current_market_rate", original))
    worst_case = max(original, market)

    get_db().record_vendor_call(loan_id, "pricing", "release")
    result = {
        "lock_id": lock.get("lock_id"),
        "status": "released",
        "original_rate": original,
        "current_market_rate": market,
        "relock_rate_if_reapplied": worst_case,
        "note": (
            "relock subject to WORST-CASE PRICING — the borrower is not restored to "
            f"the original {original}%, they relock at {worst_case}%"
        ),
        "restores_prior_state": False,
    }
    emit(
        "saga.compensate",
        actor="pricing.release_lock",
        loan_id=loan_id,
        original_rate=original,
        relock_rate=worst_case,
    )
    return result
