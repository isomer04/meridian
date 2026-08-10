"""Compliance preconditions enforced in code, not in a prompt.

Under TRID / Reg Z **§1026.19(e)(2)(i)(A)** a creditor may not impose *any* fee on a
consumer before the consumer has received the Loan Estimate and indicated **intent
to proceed**. There is exactly one exception, **§1026.19(e)(2)(i)(B)**: a bona fide
and reasonable fee for obtaining a credit report.

So:

    @requires_state(intent_to_proceed=True)   # order_appraisal — $600, gated
    # pull_credit                             — NOT gated; §(e)(2)(i)(B) exception

The point to make out loud: this is not a guideline in a system prompt that an agent
could be talked around. The $600 order is mechanically impossible until the
compliance precondition is in state, and the failure is a `PolicyViolation` that
propagates rather than a string the model can rephrase against.
"""

from __future__ import annotations

import functools
import re
from typing import Any, Callable

from .errors import PolicyViolation
from .events import emit

CITATIONS = {
    "intent_to_proceed": "12 CFR 1026.19(e)(2)(i)(A)",
    "le_delivered": "12 CFR 1026.19(e)(1)(iii)",
    "application_complete": "12 CFR 1026.2(a)(3)",
}


class PolicyContext:
    """The state a control is evaluated against.

    Deliberately a narrow, typed view rather than "the whole flow state": a control
    should read from a small set of facts you can enumerate in an audit.
    """

    def __init__(self, loan_id: str, facts: dict[str, Any] | None = None):
        self.loan_id = loan_id
        self.facts: dict[str, Any] = dict(facts or {})

    def get(self, name: str) -> Any:
        return self.facts.get(name)

    def set(self, name: str, value: Any) -> None:
        self.facts[name] = value

    def __repr__(self) -> str:
        return f"PolicyContext({self.loan_id}, {self.facts})"


def requires_state(**required: Any) -> Callable:
    """Gate a side effect on named facts in the loan's `PolicyContext`.

    The decorated callable must receive a `policy` keyword (or an object exposing
    `.policy`) so the control has something to read. Missing context is itself a
    violation — failing open would make the control decorative.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx: PolicyContext | None = kwargs.get("policy")
            if ctx is None:
                # First match wins — positional precedence. Without the break a later
                # argument carrying a `.policy` silently overrode an explicit context
                # passed earlier.
                for a in args:
                    if isinstance(a, PolicyContext):
                        ctx = a
                        break
                    candidate = getattr(a, "policy", None)
                    if isinstance(candidate, PolicyContext):
                        ctx = candidate
                        break
            if ctx is None:
                raise PolicyViolation(
                    fn.__name__,
                    "no policy context available; refusing to evaluate the control",
                )
            for name, expected in required.items():
                actual = ctx.get(name)
                if actual != expected:
                    emit(
                        "policy.violation",
                        actor=fn.__name__,
                        loan_id=ctx.loan_id,
                        control=name,
                        expected=expected,
                        actual=actual,
                        citation=CITATIONS.get(name),
                    )
                    raise PolicyViolation(
                        name,
                        f"{fn.__name__} requires {name}={expected!r}, state has {actual!r}",
                        citation=CITATIONS.get(name),
                    )
            return fn(*args, **kwargs)

        wrapper.__meridian_requires__ = required  # type: ignore[attr-defined]
        return wrapper

    return decorator


PROHIBITED_BASIS = {
    "race",
    "color",
    "religion",
    "national_origin",
    "sex",
    "gender",
    "marital_status",
    "age",
    "receipt_of_public_assistance",
    "ethnicity",
}


def assert_no_prohibited_basis(reasons: list[str]) -> list[str]:
    """ECOA / Reg B: a prohibited-basis attribute must not appear in a decision
    rationale. Returns the offending terms; the QC agent turns a non-empty result
    into a compliance finding rather than raising, because the *report* is the
    artifact an examiner wants.

    Matched on **word boundaries**, not substrings. A substring match reports "age"
    inside "agency" and "mortgage" — and a compliance check that cries wolf on every
    file is a check nobody reads.
    """
    hits = []
    joined = " ".join(reasons).lower()
    for term in PROHIBITED_BASIS:
        phrase = term.replace("_", "[ _]")
        if re.search(rf"(?<![a-z]){phrase}(?![a-z])", joined):
            hits.append(term)
    return sorted(hits)
