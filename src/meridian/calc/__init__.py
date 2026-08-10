"""Deterministic money math. No agent lives here, on purpose.

The decomposition rule: a thing earns an agent when it has its own judgment, its own
tool set, and its own failure mode. Everything in `calc/` is fully determined by its
inputs, so it is a function. `tests/test_calc.py` is what turns "the LLM never
computes" from a design statement into a tested claim.
"""

from .dti import DTIResult, calc_dti, calc_piti, monthly_pi
from .income import (
    IncomeResult,
    calc_rental_income,
    calc_self_employed_income,
    calc_w2_income,
)
from .ltv import LTVResult, calc_ltv, max_loan_for_ltv

__all__ = [
    "calc_w2_income",
    "calc_self_employed_income",
    "calc_rental_income",
    "IncomeResult",
    "calc_dti",
    "calc_piti",
    "monthly_pi",
    "DTIResult",
    "calc_ltv",
    "max_loan_for_ltv",
    "LTVResult",
]
