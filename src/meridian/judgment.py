"""The judgment seam.

The Flow owns control flow. This module is where *judgment* is supplied, and it exists
as an explicit interface for one reason: it lets the identical deterministic spine run
two ways.

* `StubJudgment` — rules, no model. This is what clears the build's hour-3 gate, what
  runs on a fresh clone with no API key, and what the eval harness compares against.
* `CrewJudgment` (in `crews/`) — three production agents or the eight-agent demo roster.

Because both drive the same Flow, the same routers and the same saga, a wrong outcome
can be localised: if stub and crew disagree, the judgment changed; if both fail, the
orchestration is wrong. That separation is worth more in a walkthrough than either
implementation on its own.

The stub is **not** a lookup table of expected answers. It evaluates the overlay rules
for real, against the same corpus the agents cite. If it were faking, the eval baseline
it provides would be worthless.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from .core.events import emit
from .models import (
    ADVERSE_ACTION_DAYS,
    AgentRun,
    Citation,
    Condition,
    ConditionType,
    Decision,
    IncomeType,
    LoanState,
    SixPiecesOfInformation,
)
from .rag import research_loop
from .tools import (
    LEDGER,
    set_actor,
    t_calc_self_employed_income,
    t_calc_w2_income,
    t_critique_retrieval,
    t_search_guidelines,
    t_verify_citation,
    tools_for_income_analyst,
)


class Judgment(Protocol):
    """Seven calls for eight agents — the income analyst and its specialist share one
    entry point because the handoff happens *inside* it, which is the point."""

    name: str

    def classify_intake(self, st: LoanState) -> dict[str, Any]: ...
    def analyze_income(self, st: LoanState) -> dict[str, Any]: ...
    def interpret_credit(self, st: LoanState) -> dict[str, Any]: ...
    def collateral_decision(self, st: LoanState) -> dict[str, Any]: ...
    def research(self, st: LoanState, questions: list[str]) -> dict[str, Any]: ...
    def underwrite(self, st: LoanState, research: dict[str, Any]) -> dict[str, Any]: ...
    def qc_review(self, st: LoanState) -> dict[str, Any]: ...


# -- overlay rules, evaluated for real ----------------------------------

MIN_SCORE = {"primary_residence": 660, "second_home": 700, "investment": 720}
MAX_LTV = {"primary_residence": 95.0, "second_home": 80.0, "investment": 80.0}
MIN_RESERVES = {"primary_residence": 2, "second_home": 6, "investment": 6}
MAX_DTI_W2 = 45.0
MAX_DTI_SE = 43.0
SE_EXCEPTION_MAX_DTI = 45.0
SE_EXCEPTION_MIN_SCORE = 700
SE_EXCEPTION_MIN_RESERVES = 6
SE_EXCEPTION_MIN_YEARS = 2.0

# A condition is PTD when it must clear before docs can be drawn. Routine verification
# items are PTF or PTC. Getting this mapping wrong is how "approved" and "approved with
# conditions" stop meaning anything.
PTD_KEYWORDS = (
    "letter of explanation",
    "explanation for",
    "project review",
    "condominium",
    "mortgage insurance",
    "existence of the business",
    "profit and loss",
    "tax returns",
    "sourcing",
)
PTC_KEYWORDS = ("verbal verification", "homeownership education")


def _classify_condition(text: str) -> ConditionType:
    low = text.lower()
    if any(k in low for k in PTD_KEYWORDS):
        return ConditionType.PTD
    if any(k in low for k in PTC_KEYWORDS):
        return ConditionType.PTC
    return ConditionType.PTF


class StubJudgment:
    """Deterministic judgment. Rules where an agent would reason."""

    name = "stub"

    # -- 1. intake ------------------------------------------------------

    def classify_intake(self, st: LoanState) -> dict[str, Any]:
        """Detect the six pieces of information and classify the documents.

        12 CFR 1026.2(a)(3): name, income, SSN, property address, estimated value,
        loan amount. Receipt of all six is what legally constitutes an *application*,
        starts the 3-business-day LE clock, and is the honest start point for a cycle
        time claim.
        """
        b, p, ln = st.borrower, st.property, st.loan
        has_income = bool(
            b.get("base_annual") or b.get("schedule_c_net") or b.get("k1_ordinary")
        )
        six = SixPiecesOfInformation(
            name=bool(b.get("name")),
            income=has_income,
            # `ssn_on_file`, not `ssn_last4`. The last four digits are a display field
            # and do not let you order a tri-merge, so they cannot satisfy the SSN element
            # of 1026.2(a)(3). The live intake agent read the fixture more strictly than an
            # earlier version of this rule did, and it was right.
            ssn=bool(b.get("ssn_on_file")),
            property_address=bool(p.get("address")),
            estimated_value=bool(p.get("estimated_value")),
            loan_amount=bool(ln.get("loan_amount")),
        )
        emp = (b.get("employment_type") or "").lower()
        if emp == "self_employed":
            income_type = IncomeType.SELF_EMPLOYED
        elif b.get("base_annual") and (b.get("schedule_c_net") or b.get("k1_ordinary")):
            income_type = IncomeType.MIXED
        else:
            income_type = IncomeType.W2
        return {
            "six_pieces": six,
            "income_type": income_type,
            "document_types": sorted({d.get("type", "unknown") for d in st.documents}),
            "narrative": (
                f"Application complete — all six pieces of information present. "
                f"{len(st.documents)} documents classified. Income type: {income_type.value}."
                if six.complete
                else f"Application incomplete — missing {', '.join(six.missing())}."
            ),
        }

    # -- 2 & 3. income, with the handoff --------------------------------

    def analyze_income(self, st: LoanState) -> dict[str, Any]:
        """The conditional-tool + handoff step.

        The menu is filtered from typed state before the analyst sees it. On a
        self-employed file the analyst's correct move is to hand off to the specialist
        rather than improvise a cash-flow analysis, and `delegated_to_specialist` is
        recorded so the handoff is a *measurable behaviour* rather than an anecdote.
        """
        it = st.income_type or IncomeType.W2
        menu = tools_for_income_analyst(it.value)
        delegated = False

        # A mixed file has business returns in it, so it needs the specialist for the same
        # reason a purely self-employed one does. Routing it down the W-2 path — which is
        # what happened before — dropped the Schedule C income from the qualifying figure
        # *and* let the file inherit the looser salaried DTI cap. Both errors point the
        # same way, toward approving a borrower on income the business does not support.
        if it in (IncomeType.SELF_EMPLOYED, IncomeType.MIXED):
            set_actor("income_analyst_agent")
            emit(
                "agent.start",
                actor="income_analyst_agent",
                loan_id=st.loan_id,
                menu=",".join(sorted(menu)),
                note=f"{it.value} file — delegating the business-income analysis",
            )
            delegated = True
            set_actor("self_employed_specialist_agent")
            emit("agent.start", actor="self_employed_specialist_agent", loan_id=st.loan_id)
            analysis = t_calc_self_employed_income(st.borrower)

            if it == IncomeType.MIXED:
                # Both streams qualify, so both are counted. The specialist owns the
                # business half; the wage half is a straight W-2 calculation.
                wage = t_calc_w2_income(st.borrower)
                analysis["components"] = {
                    "self_employed": analysis.get("components", {}),
                    "w2": wage.get("components", {}),
                }
                analysis["notes"] = list(analysis.get("notes", [])) + list(wage.get("notes", []))
                analysis["monthly_qualifying_income"] = round(
                    analysis["monthly_qualifying_income"] + wage["monthly_qualifying_income"], 2
                )
                analysis["method"] = "mixed_w2_and_self_employed_cash_flow"

            analysis["analyst"] = "self_employed_specialist_agent"
            analysis["handoff_reason"] = (
                "Schedule C / K-1 cash-flow analysis is outside the income analyst's "
                "scope; delegated per OV-SE-02"
            )
        else:
            set_actor("income_analyst_agent")
            emit(
                "agent.start",
                actor="income_analyst_agent",
                loan_id=st.loan_id,
                menu=",".join(sorted(menu)),
            )
            analysis = t_calc_w2_income(st.borrower)
            analysis["analyst"] = "income_analyst_agent"

        interp = []
        if analysis.get("notes"):
            interp.extend(analysis["notes"])
        gap = float(st.borrower.get("employment_gap_months", 0) or 0)
        if gap > 0:
            interp.append(
                f"Employment gap of {gap:g} months requires a written letter of explanation."
            )
        return {
            "analysis": analysis,
            "delegated_to_specialist": delegated,
            "interpretation": interp,
            "narrative": (
                f"Qualifying income {analysis['monthly_qualifying_income']:,.2f}/mo via "
                f"{analysis['method']}"
                + (" (delegated to specialist)" if delegated else "")
            ),
        }

    def interpret_credit(self, st: LoanState) -> dict[str, Any]:
        """The LLM interprets the report. The ratio was computed in `calc/dti.py`."""
        credit, dti = st.credit, st.dti
        flags: list[str] = []
        score = credit.get("qualifying_score", 0)
        occ = st.property.get("occupancy", "primary_residence")
        if score < MIN_SCORE.get(occ, 660):
            flags.append(
                f"representative score {score} is below the {MIN_SCORE.get(occ)} overlay "
                f"minimum for {occ.replace('_', ' ')} (OV-SCORE-01)"
            )
        if credit.get("derogatory"):
            flags.append(f"{len(credit['derogatory'])} derogatory item(s) require seasoning review")
        for exc in dti.get("excluded_liabilities", []):
            flags.append(f"liability treatment: {exc}")
        return {
            "flags": flags,
            "narrative": (
                f"Representative score {score} (middle of three). Back-end DTI "
                f"{dti.get('back_end_ratio')}% on {dti.get('monthly_debts')} of monthly debt. "
                "The inquiry from this tri-merge is permanent on all three reports."
            ),
        }

    # -- 5. collateral --------------------------------------------------

    def collateral_decision(self, st: LoanState) -> dict[str, Any]:
        """Genuine judgment: DU offered value acceptance — exercise it or not?

        The offer is an *option*, not an instruction (OV-VA-01). Exercising it saves
        roughly nine days and $600. Exercising it on a property whose value the data
        cannot support is how a lender ends up owning a bad valuation, so the agent
        weighs specific disqualifiers rather than taking the free option.
        """
        va = st.aus_findings.get("value_acceptance") or {}
        offered = bool(va.get("offered"))
        p = st.property
        blockers: list[str] = []
        if p.get("rural"):
            blockers.append("property is in a rural market with limited comparable sales (OV-VA-01.1)")
        if p.get("occupancy") in ("second_home", "investment"):
            blockers.append(
                f"subject is a {p.get('occupancy', '').replace('_', ' ')} (OV-VA-01.3)"
            )
        if p.get("in_disaster_area"):
            blockers.append("property is in a declared disaster area (OV-VA-01.4)")
        if p.get("unique"):
            blockers.append("property is non-conforming for its market (OV-VA-01.2)")

        if not offered:
            return {
                "offered": False,
                "exercise": False,
                "rationale": (
                    "No value acceptance offer in the DU findings"
                    + (f" — {va.get('reason')}" if va.get("reason") else "")
                    + ". A full appraisal is required per fannie:B4-1.2-01."
                ),
                "citations": [
                    (
                        "fannie",
                        "B4-1.2-01",
                        "a full interior and exterior appraisal is required unless the loan "
                        "casefile has received an offer of value acceptance through Desktop "
                        "Underwriter",
                    )
                ],
                "blockers": blockers,
            }
        if blockers:
            return {
                "offered": True,
                "exercise": False,
                "rationale": (
                    "Declining the value acceptance offer and ordering a full appraisal: "
                    + "; ".join(blockers)
                    + ". The offer is an option, not an instruction (overlays:OV-VA-01)."
                ),
                "citations": [
                    (
                        "overlays",
                        "OV-VA-01",
                        "an offer of value acceptance must not be exercised where the property "
                        "is rural, unique, a second home or investment property, in a declared "
                        "disaster area, or where anything indicates the estimated value is "
                        "unsupported",
                    ),
                    (
                        "fannie",
                        "B4-1.4-10",
                        "exercising an offer of value acceptance is at the lender's option and "
                        "the lender remains responsible for the value",
                    ),
                ],
                "blockers": blockers,
            }
        return {
            "offered": True,
            "exercise": True,
            "rationale": (
                "Exercising DU's value acceptance offer. None of the OV-VA-01 "
                "disqualifiers is present: the property is not rural, not unique, not a "
                "second home or investment property, not in a disaster area, and nothing "
                "in the file suggests the estimated value is unsupported. This removes "
                "roughly 9 days of appraisal turn time and a $600 borrower cost."
            ),
            "citations": [
                (
                    "overlays",
                    "OV-VA-01",
                    "exercising an offer of value acceptance is permitted where none of the "
                    "listed disqualifiers is present, and removes appraisal turn time and cost",
                ),
                (
                    "fannie",
                    "B4-1.4-10",
                    "value acceptance is an offer issued by Desktop Underwriter on eligible "
                    "loan casefiles and replaces the retired term appraisal waiver",
                ),
            ],
            "blockers": [],
        }

    # -- 6. research ----------------------------------------------------

    def research(self, st: LoanState, questions: list[str]) -> dict[str, Any]:
        set_actor("retrieval_step")
        findings: list[dict[str, Any]] = []
        product = st.loan.get("program", "conventional_conforming")
        for q in questions:
            # Through the gateway, so every search and critique lands in the ledger and
            # the retrieval agent's tool use is measurable.
            r = research_loop(
                q,
                product=product,
                search_fn=t_search_guidelines,
                critique_fn=t_critique_retrieval,
            )
            findings.append(
                {
                    "question": q,
                    "rounds_used": r["rounds_used"],
                    "sufficient": r["sufficient"],
                    "corpora_routed": r["corpora_routed"],
                    "citations": r["citations"],
                    "top": r["chunks"][0] if r["chunks"] else None,
                }
            )
        return {
            "findings": findings,
            "total_rounds": sum(f["rounds_used"] for f in findings),
            "all_sufficient": all(f["sufficient"] for f in findings),
        }

    # -- 7. underwrite --------------------------------------------------

    def underwrite(self, st: LoanState, research: dict[str, Any]) -> dict[str, Any]:
        """Reconcile DU findings against the internal overlays.

        This is what a human underwriter actually does, and it is the only place the
        decision is made. DU establishes agency eligibility; the overlay decides
        whether *this* lender will buy it (OV-GEN-01).
        """
        set_actor("underwriter_agent")
        occ = st.property.get("occupancy", "primary_residence")
        score = int(st.credit.get("qualifying_score", 0))
        dti = float(st.dti.get("back_end_ratio", 0))
        ltv = float(st.ltv.get("ltv", 0))
        reserves = float(st.borrower.get("reserves_months", 0) or 0)
        # Mixed files carry business income, so the self-employed overlays bind: the
        # tighter DTI cap with its exception path, and the OV-SE-01 history requirement.
        se = st.income_type in (IncomeType.SELF_EMPLOYED, IncomeType.MIXED)

        conflicts: list[dict[str, Any]] = []
        citations: list[Citation] = []
        conditions: list[Condition] = []
        hard_fails: list[str] = []

        def cite(corpus: str, section: str, claim: str, applied_to: str = "") -> None:
            """`claim` asserts what the *guideline* says and is verified against the
            corpus. `applied_to` records this file's figures and is not — the corpus
            does not contain them."""
            citations.append(
                Citation(corpus=corpus, section=section, claim=claim, applied_to=applied_to or None)
            )

        du_rec = st.aus_findings.get("recommendation", "")
        du_elig = st.aus_findings.get("eligibility", "")
        cite(
            "fannie",
            "B3-6-02",
            "agency permits a total debt-to-income ratio up to 50 percent on a loan "
            "underwritten through Desktop Underwriter",
            applied_to=f"DU returned {du_rec}/{du_elig} on this casefile",
        )

        # -- overlay: credit score
        min_score = MIN_SCORE.get(occ, 660)
        cite(
            "overlays",
            "OV-SCORE-01",
            f"overlay requires a minimum representative credit score of {min_score} on a "
            f"{occ.replace('_', ' ')}",
            applied_to=f"file has a representative score of {score}",
        )
        if score < min_score:
            hard_fails.append(
                f"representative credit score {score} is below the overlay minimum of {min_score}"
            )
            conflicts.append(
                {
                    "dimension": "credit_score",
                    "agency": f"DU {du_rec}/{du_elig}",
                    "overlay": f"minimum {min_score}",
                    "actual": score,
                    "binds": "overlay",
                    "citation": "overlays:OV-SCORE-01",
                }
            )

        # -- overlay: LTV
        max_ltv = MAX_LTV.get(occ, 95.0)
        ltv_section = "OV-LTV-03" if occ == "second_home" else ("OV-LTV-04" if occ == "investment" else "OV-LTV-01")
        cite(
            "overlays",
            ltv_section,
            f"overlay caps the maximum loan-to-value ratio at {max_ltv:g} percent on a "
            f"{occ.replace('_', ' ')}",
            applied_to=f"file is at {ltv:.2f} percent",
        )
        cite(
            "fannie",
            "B2-1.2-01",
            "loan-to-value on a purchase transaction is calculated using the lesser of "
            "the sales price or the appraised value",
            applied_to=(
                f"basis {st.ltv.get('value_basis')} — {st.ltv.get('value_basis_reason', '')}"
            ),
        )
        if ltv > max_ltv:
            hard_fails.append(
                f"loan-to-value of {ltv:.2f}% exceeds the overlay maximum of {max_ltv:g}% "
                f"for a {occ.replace('_', ' ')}, and {ltv_section} provides no exception path"
            )
            conflicts.append(
                {
                    "dimension": "ltv",
                    "agency": f"agency permits {90.0 if occ == 'second_home' else 97.0:g}% (fannie:B2-1.5-02)",
                    "overlay": f"{max_ltv:g}% maximum",
                    "actual": round(ltv, 2),
                    "binds": "overlay",
                    "citation": f"overlays:{ltv_section}",
                }
            )

        # -- overlay: DTI, including the self-employed exception path
        max_dti = MAX_DTI_SE if se else MAX_DTI_W2
        dti_section = "OV-DTI-02" if se else "OV-DTI-01"
        cite(
            "overlays",
            dti_section,
            f"overlay caps the maximum total debt-to-income ratio at {max_dti:g} percent "
            f"for a {'self-employed' if se else 'salaried'} borrower",
            applied_to=f"file is at {dti:.2f} percent",
        )
        if dti > max_dti:
            years_se = float(st.borrower.get("years_self_employed", 0) or 0)
            eligible_for_exception = (
                se
                and dti <= SE_EXCEPTION_MAX_DTI
                and score >= SE_EXCEPTION_MIN_SCORE
                and reserves >= SE_EXCEPTION_MIN_RESERVES
                and years_se >= SE_EXCEPTION_MIN_YEARS
            )
            conflicts.append(
                {
                    "dimension": "dti",
                    "agency": f"DU {du_rec}/{du_elig} — agency permits up to 50% (fannie:B3-6-02)",
                    "overlay": f"{max_dti:g}% maximum",
                    "actual": round(dti, 2),
                    "binds": "overlay",
                    "citation": f"overlays:{dti_section}",
                    "exception_granted": eligible_for_exception,
                }
            )
            if eligible_for_exception:
                conditions.append(
                    Condition(
                        kind=ConditionType.PTD,
                        description=(
                            f"Exception to OV-DTI-02 recorded: DTI {dti:.2f}% exceeds the 43% "
                            f"self-employed cap but does not exceed 45%. Compensating factors "
                            f"documented — representative score {score} (>= 700), reserves "
                            f"{reserves:g} months (>= 6), self-employment history {years_se:g} "
                            f"years (>= 2). Underwriter rationale required in file per OV-GEN-02."
                        ),
                        citation="overlays:OV-DTI-02",
                    )
                )
            else:
                hard_fails.append(
                    f"debt-to-income of {dti:.2f}% exceeds the overlay maximum of {max_dti:g}% "
                    + (
                        "and the OV-DTI-02 exception criteria are not met"
                        if se
                        else "and OV-DTI-01 provides no exception path"
                    )
                )

        # -- overlay: reserves
        min_res = MIN_RESERVES.get(occ, 2)
        if reserves < min_res:
            cite(
                "overlays",
                "OV-RES-01",
                f"overlay requires minimum reserves of {min_res} months of the qualifying "
                f"monthly housing expense on a {occ.replace('_', ' ')}",
                applied_to=f"file documents {reserves:g} months",
            )
            hard_fails.append(
                f"reserves of {reserves:g} months are below the overlay minimum of {min_res} months"
            )
            conflicts.append(
                {
                    "dimension": "reserves",
                    "agency": "agency requires 2 months on a second home (fannie:B3-4.1-01)",
                    "overlay": f"{min_res} months",
                    "actual": reserves,
                    "binds": "overlay",
                    "citation": "overlays:OV-RES-01",
                }
            )

        # -- overlay: self-employment history
        if se:
            years_se = float(st.borrower.get("years_self_employed", 0) or 0)
            cite(
                "overlays",
                "OV-SE-01",
                "overlay requires a minimum of two years of self-employment history "
                "documented with signed individual returns and a year-to-date profit and "
                "loss statement",
                applied_to=f"file shows {years_se:g} years",
            )
            if years_se < 2:
                hard_fails.append(
                    f"self-employment history of {years_se:g} years is below the two-year "
                    "overlay minimum"
                )

        # -- condominium
        if st.property.get("type") == "condominium":
            cite(
                "overlays",
                "OV-COND-01",
                "a full condominium project review is required regardless of the AUS "
                "recommendation and must be in the file prior to docs",
            )
            conditions.append(
                Condition(
                    kind=ConditionType.PTD,
                    description="Obtain a full condominium project review confirming warrantability.",
                    citation="overlays:OV-COND-01",
                )
            )

        # -- MI
        if st.ltv.get("mi_required"):
            cite(
                "fannie",
                "B7-1-01",
                "mortgage insurance is required on any loan with a loan-to-value above 80 percent",
            )
            conditions.append(
                Condition(
                    kind=ConditionType.PTD,
                    description="Provide evidence of the mortgage insurance commitment at the required coverage.",
                    citation="fannie:B7-1-01",
                )
            )

        # -- DU's own conditions and verification messages become file conditions
        for text in st.aus_findings.get("conditions", []):
            conditions.append(
                Condition(
                    kind=_classify_condition(text),
                    description=text,
                    citation=f"DU findings, casefile {st.aus_findings.get('casefile_id', 'unknown')}",
                )
            )
        for text in st.aus_findings.get("verification_messages", []):
            if "explanation" in text.lower() or "gap" in text.lower():
                conditions.append(
                    Condition(kind=ConditionType.PTD, description=text, citation="fannie:B3-3.1-02")
                )

        # -- income-analysis interpretation that needs documenting
        for note in st.income_analysis.get("interpretation", []) or []:
            if "letter of explanation" in note.lower():
                conditions.append(
                    Condition(kind=ConditionType.PTD, description=note, citation="fannie:B3-3.1-02")
                )

        # -- the decision
        if hard_fails:
            decision = Decision.DENIED
            rationale = (
                f"DENIED. Desktop Underwriter returned {du_rec}/{du_elig}, which establishes "
                "agency eligibility only — it does not waive an overlay (overlays:OV-GEN-01). "
                "The file fails on: " + "; ".join(hard_fails) + "."
            )
            adverse = hard_fails
        elif any(c.kind == ConditionType.PTD for c in conditions):
            decision = Decision.APPROVED_WITH_CONDITIONS
            rationale = (
                f"APPROVED WITH CONDITIONS. DU returned {du_rec}/{du_elig} and the file clears "
                "every applicable overlay"
                + (
                    " (one via the documented OV-DTI-02 exception path, with compensating "
                    "factors recorded as a prior-to-docs condition)"
                    if any(c.get("exception_granted") for c in conflicts)
                    else ""
                )
                + f". {sum(1 for c in conditions if c.kind == ConditionType.PTD)} prior-to-docs "
                f"condition(s) must clear before docs are drawn."
            )
            adverse = []
        else:
            decision = Decision.APPROVED
            rationale = (
                f"APPROVED. DU returned {du_rec}/{du_elig} and the file clears every applicable "
                f"overlay with margin: score {score} against a {min_score} minimum, LTV "
                f"{ltv:.2f}% against {max_ltv:g}%, DTI {dti:.2f}% against {max_dti:g}%, reserves "
                f"{reserves:g} months against {min_res}. Remaining conditions are routine "
                "verification items."
            )
            adverse = []

        emit(
            "decision",
            actor="underwriter_agent",
            loan_id=st.loan_id,
            decision=decision.value,
            overlay_conflicts=len(conflicts),
            conditions=len(conditions),
        )
        return {
            "decision": decision,
            "rationale": rationale,
            "conditions": conditions,
            "citations": citations,
            "overlay_conflicts": conflicts,
            "adverse_action_reasons": adverse,
        }

    # -- 8. governance --------------------------------------------------

    def qc_review(self, st: LoanState) -> dict[str, Any]:
        """The agent that gets remembered.

        Three checks, because they fail differently:

        1. every cited section **exists** — a fabricated section number;
        2. every cited section **supports the claim** made against it — the subtler
           failure, which survives a spot check;
        3. no **prohibited-basis** attribute reached the decision (ECOA / Reg B), and
           where the file is denied, an adverse-action notice is due within 30 days.
        """
        set_actor("compliance_qc_agent")
        findings: list[dict[str, Any]] = []
        verified: list[Citation] = []

        for c in st.citations:
            v = t_verify_citation(c.corpus, c.section, c.claim)
            c.exists = v["exists"]
            c.supports_claim = v["supports_claim"]
            c.verified = v["verified"]
            c.quoted = v.get("quoted")
            c.qc_note = v["qc_note"]
            verified.append(c)
            if not v["verified"]:
                findings.append(
                    {
                        "severity": "high" if not v["exists"] else "medium",
                        "kind": "citation_not_verified",
                        "detail": f"{c.corpus}:{c.section} — {v['qc_note']}",
                    }
                )

        # ECOA / Reg B — prohibited basis must not appear in any decision input.
        from .core.policy import assert_no_prohibited_basis

        surfaces = (
            [st.decision_rationale]
            + st.adverse_action_reasons
            + [c.description for c in st.conditions]
            + [f["detail"] if isinstance(f, dict) else str(f) for f in st.qc_findings]
        )
        hits = assert_no_prohibited_basis([s for s in surfaces if s])
        if hits:
            findings.append(
                {
                    "severity": "critical",
                    "kind": "prohibited_basis",
                    "detail": (
                        "prohibited-basis attribute(s) present in the decision rationale or "
                        f"conditions: {', '.join(hits)} (overlays:OV-AA-01, ECOA/Reg B)"
                    ),
                }
            )

        if st.decision == Decision.DENIED:
            if not st.adverse_action_reasons:
                findings.append(
                    {
                        "severity": "critical",
                        "kind": "adverse_action_missing_reasons",
                        "detail": "a denied file must state the specific principal reasons relied upon",
                    }
                )
            findings.append(
                {
                    "severity": "info",
                    "kind": "adverse_action_due",
                    "detail": (
                        f"adverse action notice due within {ADVERSE_ACTION_DAYS} days of the "
                        "completed application under ECOA/Reg B (overlays:OV-AA-01)"
                    ),
                }
            )

        total = len(verified)
        ok = sum(1 for c in verified if c.verified)
        passed = not any(f["severity"] in ("critical", "high") for f in findings)
        emit(
            "agent.done",
            actor="compliance_qc_agent",
            loan_id=st.loan_id,
            citations_verified=f"{ok}/{total}",
            passed=passed,
        )
        return {
            "findings": findings,
            "passed": passed,
            "citations": verified,
            "citation_validity_rate": round(ok / total, 4) if total else None,
        }


def timed(st: LoanState, agent: str, task: str = ""):
    """Context manager recording per-agent latency and cost.

    "Which agent is expensive?" is a real operating question and the numbers are free
    to collect, so they are collected on every run rather than behind a flag.
    """

    class _T:
        def __enter__(self_inner):
            self_inner.t0 = time.perf_counter()
            self_inner.tools0 = len(LEDGER.calls)
            return self_inner

        def __exit__(self_inner, *exc):
            run = AgentRun(
                agent=agent,
                task=task,
                seconds=round(time.perf_counter() - self_inner.t0, 4),
                tools_called=[c["tool"] for c in LEDGER.calls[self_inner.tools0 :]],
            )
            st.agent_runs.append(run)
            return False

    return _T()
