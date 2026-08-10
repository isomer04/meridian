"""Worked examples for the money math.

The point of this file is to make *"the LLM never computes"* a tested claim rather
than a design statement. Every number below is checked against a figure you could
work by hand on a 1008.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meridian.calc import (  # noqa: E402
    calc_dti,
    calc_ltv,
    calc_piti,
    calc_rental_income,
    calc_self_employed_income,
    calc_w2_income,
    max_loan_for_ltv,
    monthly_pi,
)


# -- W-2 income ----------------------------------------------------------


def test_w2_base_salary_is_not_averaged():
    """You average variable income, not a salary. A raise counts immediately."""
    r = calc_w2_income(base_annual=120_000, year_history=[{"year": 2024}, {"year": 2023}])
    assert r.monthly_qualifying == 10_000.00
    assert r.components["base_monthly"] == 10_000.00


def test_w2_bonus_and_overtime_are_averaged_over_documented_history():
    r = calc_w2_income(
        base_annual=96_000,
        year_history=[{"year": 2023}, {"year": 2024}],
        bonus_history=[10_000, 14_000],  # 24k / 2 years / 12 = 1,000/mo
        overtime_history=[6_000, 6_000],  # 12k / 2 / 12 = 500/mo
    )
    assert r.components["base_monthly"] == 8_000.00
    assert r.components["bonus_monthly"] == 1_000.00
    assert r.components["overtime_monthly"] == 500.00
    assert r.monthly_qualifying == 9_500.00


def test_declining_variable_income_uses_the_most_recent_year_not_the_average():
    r = calc_w2_income(
        base_annual=96_000,
        year_history=[{"year": 2023}, {"year": 2024}],
        bonus_history=[24_000, 12_000],  # average 1,500/mo, most recent 1,000/mo
    )
    assert r.components["bonus_monthly"] == 1_000.00
    assert any("declining" in n for n in r.notes)


def test_two_year_w2_average_with_an_employment_gap():
    """A gap over six months makes the variable history unreliable, so it drops out
    and the file is flagged. Base salary at the current rate survives."""
    r = calc_w2_income(
        base_annual=90_000,
        year_history=[{"year": 2023}, {"year": 2024}],
        bonus_history=[12_000, 12_000],
        overtime_history=[6_000, 6_000],
        employment_gap_months=8,
    )
    assert r.monthly_qualifying == 7_500.00, "only base salary should qualify"
    assert r.components["bonus_monthly"] == 0.0
    assert r.components["overtime_monthly"] == 0.0
    assert any("gap of 8 months" in n and "excluded" in n for n in r.notes)


def test_short_gap_does_not_disqualify_variable_income():
    r = calc_w2_income(
        base_annual=90_000, bonus_history=[12_000, 12_000], employment_gap_months=3
    )
    assert r.components["bonus_monthly"] == 1_000.00
    assert any("gap of 3 months" in n for n in r.notes)


def test_single_year_history_is_flagged():
    r = calc_w2_income(base_annual=90_000, year_history=[{"year": 2024}])
    assert any("2 years preferred" in n for n in r.notes)


# -- self-employed -------------------------------------------------------


def test_schedule_c_add_backs():
    """Net profit 80k and 85k, plus 9k of non-cash add-backs, less 1k of real cash
    out. Adjusted: 88k and 93k → average 90.5k → 7,541.67/mo."""
    r = calc_self_employed_income(
        schedule_c_net=[80_000, 85_000],
        add_backs={"depreciation": 6_000, "business_use_of_home": 2_500, "meals_50pct": 500},
        deductions={"nonrecurring_income": 1_000},
    )
    assert r.components["add_backs_annual"] == 9_000.0
    assert r.components["used_annual"] == 90_500.0
    assert r.monthly_qualifying == 7_541.67


def test_declining_business_uses_most_recent_year():
    """Averaging a declining business overstates capacity. This is the specific
    error the cash-flow analysis exists to avoid."""
    r = calc_self_employed_income(
        schedule_c_net=[120_000, 60_000], add_backs={"depreciation": 6_000}
    )
    assert r.components["used_annual"] == 66_000.0  # 60k + 6k, not the 99k average
    assert r.monthly_qualifying == 5_500.00
    assert any("declining" in n for n in r.notes)


def test_k1_and_schedule_c_combine_per_year():
    r = calc_self_employed_income(schedule_c_net=[40_000, 40_000], k1_ordinary=[20_000, 20_000])
    assert r.components["used_annual"] == 60_000.0


def test_one_year_of_returns_is_flagged():
    r = calc_self_employed_income(schedule_c_net=[100_000])
    assert any("less than 2 years" in n for n in r.notes)


def test_no_returns_yields_zero_not_a_crash():
    r = calc_self_employed_income()
    assert r.monthly_qualifying == 0.0


def test_rental_income_haircut():
    r = calc_rental_income(2_400)
    assert r.monthly_qualifying == 1_800.00


# -- payment and DTI -----------------------------------------------------


def test_monthly_pi_matches_a_hand_amortization():
    # 400,000 at 6.5% over 30 years = 2,528.27
    assert monthly_pi(400_000, 6.5, 30) == pytest.approx(2_528.27, abs=0.02)


def test_zero_rate_degrades_to_straight_line():
    assert monthly_pi(360_000, 0.0, 30) == 1_000.00


def test_piti_includes_hoa_and_mi():
    p = calc_piti(
        loan_amount=400_000,
        annual_rate_pct=6.5,
        annual_property_tax=6_000,
        annual_hazard_insurance=1_800,
        monthly_hoa=250,
        monthly_mi=140,
    )
    assert p["taxes"] == 500.00
    assert p["hazard_insurance"] == 150.00
    assert p["total_piti"] == pytest.approx(3_568.27, abs=0.02)


def test_dti_front_and_back_end():
    """Income 10,000/mo, PITI 3,000, debts 700 → 30.00 / 37.00."""
    r = calc_dti(
        monthly_qualifying_income=10_000,
        piti=3_000,
        liabilities=[
            {"creditor": "auto", "type": "installment", "monthly_payment": 450},
            {"creditor": "card", "type": "revolving", "monthly_payment": 250},
        ],
    )
    assert r.front_end == 30.00
    assert r.back_end == 37.00
    assert r.monthly_debts == 700.00


def test_revolving_with_no_stated_minimum_imputes_five_percent():
    r = calc_dti(
        monthly_qualifying_income=10_000,
        piti=2_000,
        liabilities=[{"creditor": "card", "type": "revolving", "balance": 4_000}],
    )
    assert r.components["card"] == 200.00


def test_deferred_student_loan_imputes_half_a_percent():
    r = calc_dti(
        monthly_qualifying_income=10_000,
        piti=2_000,
        liabilities=[
            {
                "creditor": "navient",
                "type": "deferred_student_no_payment",
                "balance": 60_000,
            }
        ],
    )
    assert r.components["navient"] == 300.00
    assert any("imputed" in e for e in r.excluded)


def test_authorized_user_tradeline_is_excluded_with_a_reason():
    r = calc_dti(
        monthly_qualifying_income=10_000,
        piti=2_000,
        liabilities=[{"creditor": "spouse card", "type": "authorized_user", "monthly_payment": 400}],
    )
    assert r.monthly_debts == 0.0
    assert "authorized-user" in r.excluded[0]


def test_dti_requires_positive_income():
    with pytest.raises(ValueError):
        calc_dti(monthly_qualifying_income=0, piti=2_000)


# -- LTV -----------------------------------------------------------------


def test_ltv_uses_the_lesser_of_price_or_value():
    """The rule that catches people out. Appraisal below contract price does not get
    to use the contract price."""
    r = calc_ltv(loan_amount=380_000, purchase_price=475_000, appraised_value=440_000)
    assert r.basis == 440_000
    assert r.ltv == pytest.approx(86.36, abs=0.01)
    assert "below contract price" in r.basis_reason
    assert r.mi_required


def test_high_appraisal_does_not_create_equity():
    r = calc_ltv(loan_amount=380_000, purchase_price=475_000, appraised_value=520_000)
    assert r.basis == 475_000
    assert r.ltv == 80.00
    assert "not equity" in r.basis_reason
    assert not r.mi_required


def test_low_appraisal_reports_the_cash_gap():
    r = calc_ltv(loan_amount=380_000, purchase_price=475_000, appraised_value=440_000)
    assert any("35,000" in n for n in r.notes)


def test_cltv_includes_subordinate_liens():
    r = calc_ltv(
        loan_amount=380_000,
        purchase_price=500_000,
        appraised_value=500_000,
        subordinate_liens=45_000,
    )
    assert r.ltv == 76.00
    assert r.cltv == 85.00


def test_refinance_uses_appraised_value():
    r = calc_ltv(loan_amount=300_000, appraised_value=400_000, transaction_type="refinance")
    assert r.basis == 400_000
    assert r.ltv == 75.00


def test_max_loan_for_ltv_is_the_counter_offer_figure():
    assert max_loan_for_ltv(440_000, 80.0) == 352_000.0
