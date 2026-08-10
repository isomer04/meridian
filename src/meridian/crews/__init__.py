"""`CrewJudgment` — three production agents plus the eight-agent demo roster.

Both rosters satisfy the same `Judgment` protocol
`StubJudgment` satisfies. The Flow, the routers, the saga, the tool gateway and the policy
controls are all identical either way. Only the judgment changes.

That is the useful property. If a scenario comes out wrong under `--judgment crew` but
right under `--judgment stub`, the judgment is the problem. If it comes out wrong under
both, the orchestration is. Most agent systems cannot tell you which.

The demo eight (production retains roles 5, 7 and 8):

  1. intake_agent                     — the six pieces of information (Intake)
  2. income_analyst_agent             — conditional tool menu, delegates (Verify)
  3. self_employed_specialist_agent   — handoff target, cash-flow analysis (Verify)
  4. credit_liability_agent           — interprets the tri-merge; never computes (Verify)
  5. collateral_agent                 — exercise value acceptance, or don't (Verify)
  6. guideline_research_agent         — agentic RAG over three corpora (Underwrite)
  7. underwriter_agent                — reconciles DU against overlays (Underwrite)
  8. compliance_qc_agent              — citation + ECOA control (Governance)

Designed and deliberately not built: `aus_submission_agent`, `title_agent`, a standalone
`document_classifier`, a standalone `conditions_agent`. Reasons in docs/architecture.md.
"""

from __future__ import annotations

from typing import Any

from ..core.events import emit
from ..core.policy import assert_no_prohibited_basis
from ..rag import route
from ..models import (
    ADVERSE_ACTION_DAYS,
    Citation,
    Condition,
    ConditionType,
    Decision,
    IncomeType,
    LoanState,
    SixPiecesOfInformation,
)
from ..tools import LEDGER, set_actor, t_verify_citation
from .governance.crew import GovernanceCrew
from .intake.crew import IntakeCrew
from .llm import METER, build_llm
from .underwrite.crew import UnderwriteCrew
from .verify.crew import VerifyCrew

__all__ = ["CrewJudgment", "METER"]


class CrewJudgment:
    """LLM judgment with a three-agent production or eight-agent demo roster."""

    name = "crew"

    PRODUCTION_AGENTS = ("collateral_agent", "underwriter_agent", "compliance_qc_agent")
    DEMO_AGENTS = (
        "intake_agent",
        "income_analyst_agent",
        "self_employed_specialist_agent",
        "credit_liability_agent",
        "collateral_agent",
        "guideline_research_agent",
        "underwriter_agent",
        "compliance_qc_agent",
    )

    def __init__(
        self,
        mode: str = "replay",
        model: str | None = None,
        scenario: str = "",
        roster: str = "production",
    ):
        if roster not in {"production", "demo"}:
            raise ValueError("roster must be 'production' or 'demo'")
        self.mode = mode
        self.model = model
        self.scenario = scenario
        self.roster = roster
        self._llm = None

    @property
    def active_agents(self) -> tuple[str, ...]:
        return self.DEMO_AGENTS if self.roster == "demo" else self.PRODUCTION_AGENTS

    def bind(self, scenario: str) -> None:
        """Cassettes are keyed by scenario, so the LLM is built once the loan is known."""
        self.scenario = scenario
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = build_llm(self.mode, self.scenario or "default", self.model)
        return self._llm

    # -- 1 -------------------------------------------------------------

    def classify_intake(self, st: LoanState) -> dict[str, Any]:
        if self.roster == "production":
            from ..judgment import StubJudgment

            self.bind(st.scenario_name or st.loan_id)
            return StubJudgment().classify_intake(st)
        self.bind(st.scenario_name or st.loan_id)
        set_actor("intake_agent")
        out = IntakeCrew(self.llm).run(st.borrower, st.property, st.loan, st.documents)
        six = SixPiecesOfInformation(
            name=out.name_present,
            income=out.income_present,
            ssn=out.ssn_present,
            property_address=out.property_address_present,
            estimated_value=out.estimated_value_present,
            loan_amount=out.loan_amount_present,
        )
        try:
            income_type = IncomeType(out.income_type.strip().lower())
        except ValueError:
            # A malformed enum is a real failure and is recorded, not smoothed over.
            st.errors.append(f"intake_agent returned an unrecognised income_type: {out.income_type!r}")
            income_type = IncomeType.W2
        return {
            "six_pieces": six,
            "income_type": income_type,
            "document_types": out.document_types,
            "narrative": out.narrative,
        }

    # -- 2 & 3 ---------------------------------------------------------

    def analyze_income(self, st: LoanState) -> dict[str, Any]:
        if self.roster == "production":
            return self._analyze_income_directly(st)
        it = st.income_type or IncomeType.W2
        set_actor("income_analyst_agent")
        out = VerifyCrew(self.llm).run_income(it.value, st.borrower)
        claimed = bool(out.delegated_to_specialist)
        observed = any(
            call["actor"] == "self_employed_specialist_agent"
            and call["tool"] == "calc_self_employed_income"
            for call in LEDGER.calls
        )
        if claimed != observed:
            finding = {
                "severity": "medium",
                "kind": "handoff_claim_mismatch",
                "detail": (
                    "income_analyst_agent claimed delegated_to_specialist="
                    f"{claimed}, but the tool ledger observed {observed}"
                ),
                "claimed": claimed,
                "observed": observed,
            }
            st.behavior_findings.append(finding)
            emit("behavior.mismatch", actor="income_analyst_agent", loan_id=st.loan_id, **finding)

        analysis = {
            "monthly_qualifying_income": out.monthly_qualifying_income,
            "method": out.method,
            "analyst": (
                "self_employed_specialist_agent" if observed else "income_analyst_agent"
            ),
            "notes": out.interpretation,
        }
        if observed:
            analysis["handoff_reason"] = (
                "Schedule C / K-1 cash-flow analysis is outside the income analyst's scope"
            )
        return {
            "analysis": analysis,
            "delegated_to_specialist": observed,
            "delegated_to_specialist_claim": claimed,
            "interpretation": out.interpretation,
            "narrative": out.narrative,
        }

    def _analyze_income_directly(self, st: LoanState) -> dict[str, Any]:
        """Dispatch typed income directly to calculators; no analyst agents involved."""
        from ..tools import t_calc_self_employed_income, t_calc_w2_income

        it = st.income_type or IncomeType.W2
        set_actor("income_validation_step")
        if it == IncomeType.W2:
            analysis = t_calc_w2_income(st.borrower)
        else:
            analysis = t_calc_self_employed_income(st.borrower)
            if it == IncomeType.MIXED:
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
        analysis["analyst"] = "income_validation_step"
        interpretation = list(analysis.get("notes", []))
        gap = float(st.borrower.get("employment_gap_months", 0) or 0)
        if gap > 0:
            interpretation.append(
                f"Employment gap of {gap:g} months requires a written letter of explanation."
            )
        return {
            "analysis": analysis,
            "delegated_to_specialist": False,
            "delegated_to_specialist_claim": None,
            "interpretation": interpretation,
            "narrative": (
                f"Qualifying income {analysis['monthly_qualifying_income']:,.2f}/mo via "
                f"{analysis['method']} (deterministic production dispatch)"
            ),
        }

    # -- 4 -------------------------------------------------------------

    def interpret_credit(self, st: LoanState) -> dict[str, Any]:
        if self.roster == "production":
            from ..judgment import StubJudgment

            return StubJudgment().interpret_credit(st)
        set_actor("credit_liability_agent")
        out = VerifyCrew(self.llm).run_credit(
            st.credit, st.dti, st.ltv, st.property.get("occupancy", "primary_residence")
        )
        return {"flags": out.flags, "narrative": out.narrative}

    # -- 5 -------------------------------------------------------------

    def collateral_decision(self, st: LoanState) -> dict[str, Any]:
        set_actor("collateral_agent")
        out = VerifyCrew(self.llm).run_collateral(st.aus_findings, st.property)
        citations = [
            (c.corpus.strip().lower(), c.section.strip(), c.claim) for c in out.citations
        ]
        return {
            "offered": bool((st.aus_findings.get("value_acceptance") or {}).get("offered")),
            "exercise": out.exercise_value_acceptance,
            "rationale": out.rationale,
            "citations": citations,
            "blockers": out.blockers,
        }

    # -- 6 -------------------------------------------------------------

    def research(self, st: LoanState, questions: list[str]) -> dict[str, Any]:
        if self.roster == "production":
            from ..judgment import StubJudgment

            return StubJudgment().research(st, questions)
        set_actor("guideline_research_agent")
        program = st.loan.get("program", "conventional_conforming")
        out = UnderwriteCrew(self.llm).run_research(questions, program)
        # Routing is deterministic — the same `route()` the search tool applies — so it can
        # be reported truthfully. It used to be hardcoded to ["fannie", "overlays"], which
        # reported an FHA file as having been routed to the agency corpus it never touched.
        corpora_routed = route(program)
        findings = [
            {
                "question": f.question,
                "answer": f.answer,
                "citations": f.citations,
                "sufficient": f.sufficient,
                "rounds_used": f.rounds_used,
                "corpora_routed": corpora_routed,
            }
            for f in out.findings
        ]
        return {
            "findings": findings,
            "total_rounds": sum(f["rounds_used"] for f in findings),
            "all_sufficient": all(f["sufficient"] for f in findings) if findings else False,
        }

    # -- 7 -------------------------------------------------------------

    def underwrite(self, st: LoanState, research: dict[str, Any]) -> dict[str, Any]:
        set_actor("underwriter_agent")
        out = UnderwriteCrew(self.llm).run_underwrite(
            {
                "aus_findings": st.aus_findings,
                "monthly_income": st.income_analysis.get("monthly_qualifying_income"),
                "back_end_dti": st.dti.get("back_end_ratio"),
                "ltv": st.ltv.get("ltv"),
                "score": st.credit.get("qualifying_score"),
                "reserves": st.borrower.get("reserves_months", 0),
                "occupancy": st.property.get("occupancy", "primary_residence"),
                "property_type": st.property.get("type", ""),
                "income_type": st.income_type.value if st.income_type else "w2",
                "years_self_employed": st.borrower.get("years_self_employed", 0),
                "mi_required": st.ltv.get("mi_required", False),
                "research": research["findings"],
                "income_interpretation": st.income_analysis.get("interpretation", []),
                "credit_interpretation": st.credit.get("interpretation", {}),
            }
        )
        try:
            decision = Decision(out.decision.strip().lower())
        except ValueError:
            st.errors.append(f"underwriter_agent returned an unrecognised decision: {out.decision!r}")
            decision = Decision.SUSPENDED

        conditions = []
        for c in out.conditions:
            try:
                kind = ConditionType(c.kind.strip().lower())
            except ValueError:
                kind = ConditionType.PTF
            conditions.append(
                Condition(kind=kind, description=c.description, citation=c.citation)
            )

        citations = [
            Citation(
                corpus=c.corpus.strip().lower(),
                section=c.section.strip(),
                claim=c.claim,
                applied_to=c.applied_to or None,
            )
            for c in out.citations
        ]
        emit(
            "decision",
            actor="underwriter_agent",
            loan_id=st.loan_id,
            decision=decision.value,
            overlay_conflicts=len(out.overlay_conflicts),
            conditions=len(conditions),
        )
        return {
            "decision": decision,
            "rationale": out.rationale,
            "conditions": conditions,
            "citations": citations,
            "overlay_conflicts": [c.model_dump() for c in out.overlay_conflicts],
            "adverse_action_reasons": out.adverse_action_reasons,
        }

    # -- 8 -------------------------------------------------------------

    def qc_review(self, st: LoanState) -> dict[str, Any]:
        """Hybrid on purpose.

        The per-citation verdict is deterministic — the same `verify_citation` function
        the agent calls as a tool is run here to stamp each `Citation` object, so the
        citation-validity metric never depends on whether the agent remembered to call
        the tool on every one. The *agent's* contribution is the prohibited-basis read
        and the judgment on whether stated adverse-action reasons are specific enough to
        act on, which a keyword list cannot settle.

        The deterministic floor is also kept: if the agent misses a prohibited-basis term
        or an empty adverse-action list, the Python check still catches it. A control you
        can only rely on when the model cooperates is not a control.
        """
        set_actor("compliance_qc_agent")
        for c in st.citations:
            v = t_verify_citation(c.corpus, c.section, c.claim)
            c.exists, c.supports_claim, c.verified = v["exists"], v["supports_claim"], v["verified"]
            c.quoted, c.qc_note = v.get("quoted"), v["qc_note"]
            # Fixed order: existence -> lexical support -> semantic entailment.  The
            # model is never asked to rescue a citation rejected by deterministic code.
            if c.verified:
                verdict = GovernanceCrew(self.llm).run_entailment(
                    v["section_text"], c.claim
                )
                c.entailed = verdict.entailed
                if not verdict.entailed:
                    c.verified = False
                    c.qc_note = f"entailment judge rejected claim: {verdict.rationale}"

        out = GovernanceCrew(self.llm).run_qc(
            {
                "decision": st.decision.value if st.decision else "none",
                "rationale": st.decision_rationale,
                "citations": [
                    {"corpus": c.corpus, "section": c.section, "claim": c.claim, "applied_to": c.applied_to}
                    for c in st.citations
                ],
                "conditions": [{"kind": c.kind.value, "description": c.description} for c in st.conditions],
                "adverse_action_reasons": st.adverse_action_reasons,
                # One constant, so the prompt and the notice's `due_on` cannot drift apart.
                "adverse_action_days": ADVERSE_ACTION_DAYS,
            }
        )
        findings = [f.model_dump() for f in out.findings]

        # -- the deterministic floor, regardless of what the agent reported
        for c in st.citations:
            if not c.verified and not any(c.section in f["detail"] for f in findings):
                findings.append(
                    {
                        "severity": "high" if not c.exists else "medium",
                        "kind": "citation_not_verified",
                        "detail": f"{c.corpus}:{c.section} — {c.qc_note}",
                    }
                )
        hits = assert_no_prohibited_basis(
            [st.decision_rationale, *st.adverse_action_reasons, *(c.description for c in st.conditions)]
        )
        if hits and not any(f["kind"] == "prohibited_basis" for f in findings):
            findings.append(
                {
                    "severity": "critical",
                    "kind": "prohibited_basis",
                    "detail": f"prohibited-basis attribute(s) present: {', '.join(hits)} (ECOA/Reg B)",
                }
            )
        if st.decision == Decision.DENIED:
            if not st.adverse_action_reasons and not any(
                f["kind"] == "adverse_action_missing_reasons" for f in findings
            ):
                findings.append(
                    {
                        "severity": "critical",
                        "kind": "adverse_action_missing_reasons",
                        "detail": "a denied file must state the specific principal reasons relied upon",
                    }
                )
            if not any(f["kind"] == "adverse_action_due" for f in findings):
                findings.append(
                    {
                        "severity": "info",
                        "kind": "adverse_action_due",
                        "detail": (
                            f"adverse action notice due within {ADVERSE_ACTION_DAYS} days under "
                            "ECOA/Reg B (overlays:OV-AA-01)"
                        ),
                    }
                )

        total = len(st.citations)
        ok = sum(1 for c in st.citations if c.verified)
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
            "citations": st.citations,
            "citation_validity_rate": round(ok / total, 4) if total else None,
        }
