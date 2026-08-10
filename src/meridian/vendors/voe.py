"""Verification of employment / deposit.

Kept simple on purpose — it exists so the cycle-time model has an honest
non-compressible step in it. Employer response time is the constraint, and no amount
of agent orchestration moves it. Naming what *doesn't* compress is what makes the
rest of the number credible.
"""

from __future__ import annotations

from typing import Any

from ..core.db import get_db
from ..core.errors import VendorError
from ..core.events import emit
from .fixtures import fixture_for


def verify_employment(loan_id: str, employer: str = "", **_: Any) -> dict[str, Any]:
    fx = fixture_for(loan_id, "voe") or {}
    if fx.get("_fail"):
        raise VendorError("voe", fx["_fail"])
    get_db().record_vendor_call(loan_id, "voe", "voe")
    result = {
        "vendor": "VOE service",
        "employer": employer or fx.get("employer", "unknown"),
        "status": fx.get("status", "verified"),
        "start_date": fx.get("start_date"),
        "probability_of_continued_employment": fx.get("continuance", "likely"),
        "response_days": fx.get("response_days", 3),
        "note": "employer response time does not compress",
    }
    emit("tool.result", actor="voe.verify_employment", loan_id=loan_id, status=result["status"])
    return result


def verify_deposits(loan_id: str, **_: Any) -> dict[str, Any]:
    fx = fixture_for(loan_id, "vod") or {}
    get_db().record_vendor_call(loan_id, "voe", "vod")
    if not fx:
        raise VendorError("vod", "no asset accounts on file", retryable=False)
    if fx.get("_fail"):
        # A failing fixture used to fall through to the defaults below and return
        # `total_verified_assets: 0` — indistinguishable from a real borrower with no
        # money, and reserves are an overlay the file is denied on.
        raise VendorError("vod", fx["_fail"])
    result = {
        "accounts": fx.get("accounts", []),
        "total_verified_assets": fx.get("total_verified_assets", 0),
        "large_deposits_needing_sourcing": fx.get("large_deposits", []),
    }
    emit(
        "tool.result",
        actor="voe.verify_deposits",
        loan_id=loan_id,
        assets=result["total_verified_assets"],
    )
    return result
