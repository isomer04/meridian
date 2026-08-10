"""Appraisal Management Company.

This is the expensive, gated, partially-compensatable step, and it carries the single
best line in the demo for the least work.

Gating: **`@requires_state(intent_to_proceed=True)`**. Under TRID / Reg Z
§1026.19(e)(2)(i)(A) a creditor may not impose any fee before the consumer has
received the Loan Estimate and indicated intent to proceed. So the $600 order is not
discouraged by a prompt — it is *mechanically impossible* until the compliance
precondition is in state, and the failure is a `PolicyViolation` that propagates.

Compensation: `cancel_appraisal_order` refunds only **pre-inspection**. Once the
appraiser has been to the property the $600 is spent, and the saga records that
honestly rather than pretending the money came back.
"""

from __future__ import annotations

from typing import Any

from ..core.db import get_db
from ..core.errors import VendorError
from ..core.events import emit
from ..core.policy import PolicyContext, requires_state
from .fixtures import fixture_for

APPRAISAL_FEE = 600


@requires_state(intent_to_proceed=True)
def order_appraisal(
    loan_id: str, policy: PolicyContext, property_address: str = "", **_: Any
) -> dict[str, Any]:
    """Order a full appraisal. $600, chargeable to the borrower, gated on Reg Z."""
    fx = fixture_for(loan_id, "amc") or {}
    if fx.get("_fail"):
        raise VendorError("amc", fx["_fail"])

    db = get_db()
    db.record_vendor_call(loan_id, "amc", "order")

    order = {
        "vendor": "AMC",
        "order_id": fx.get("order_id", f"AMC-{loan_id}"),
        "fee": APPRAISAL_FEE,
        "status": "ordered",
        "inspection_complete": False,
        "turn_time_days": fx.get("turn_time_days", 9),
        "appraised_value": fx.get("appraised_value"),
        "property_address": property_address,
        "compliance": "fee permitted — intent to proceed on file per 1026.19(e)(2)(i)(A)",
    }
    emit(
        "tool.call",
        actor="amc.order_appraisal",
        loan_id=loan_id,
        fee=APPRAISAL_FEE,
        turn_time_days=order["turn_time_days"],
    )
    return order


def cancel_appraisal_order(loan_id: str, forward_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compensating action. Refundable pre-inspection only.

    The honest outcome matters more than a clean one: if the inspection happened, the
    money is gone and the saga row says so.
    """
    order = forward_result or {}
    inspected = bool(order.get("inspection_complete"))
    get_db().record_vendor_call(loan_id, "amc", "cancel")
    result = {
        "order_id": order.get("order_id"),
        "status": "cancelled",
        "refunded": 0 if inspected else APPRAISAL_FEE,
        "unrecovered_cost": APPRAISAL_FEE if inspected else 0,
        "note": (
            "inspection already performed — $600 is not recoverable"
            if inspected
            else "cancelled pre-inspection — fee refunded in full"
        ),
    }
    emit("saga.compensate", actor="amc.cancel_appraisal_order", loan_id=loan_id, **{
        "refunded": result["refunded"],
    })
    return result


def receive_appraisal(loan_id: str, order: dict[str, Any] | None = None) -> dict[str, Any]:
    """The report comes back. In scenario 3 it comes back low, and the LTV breach
    that follows is computed in `calc/ltv.py`, not reasoned about by a model."""
    fx = fixture_for(loan_id, "amc") or {}
    value = fx.get("appraised_value")
    if value is None:
        raise VendorError("amc", "appraisal report not yet available", retryable=True)
    report = {
        "order_id": (order or {}).get("order_id", fx.get("order_id")),
        "appraised_value": value,
        "condition": fx.get("condition", "C3"),
        "inspection_complete": True,
        "comparables": fx.get("comparables", 3),
        "notes": fx.get("appraisal_notes", ""),
    }
    emit("tool.result", actor="amc.receive_appraisal", loan_id=loan_id, appraised_value=value)
    return report
