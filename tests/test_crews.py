"""Crew wiring, tested without a network call.

`ScriptedLLM` returns a canned final answer per task. That is enough to prove the things
that are wiring rather than judgment, and those are the things that break silently:

  * every crew constructs from its YAML and its tasks bind to the right agents;
  * the citation guardrail rejects a fabricated section and accepts a real one;
  * `CrewJudgment` satisfies the same `Judgment` protocol as `StubJudgment`, so the
    identical Flow, routers and saga run either way;
  * the deterministic QC floor still catches a prohibited-basis term and an unverifiable
    citation even when the agent's own report misses them.

What this file explicitly does **not** test is whether the agents *reason* well — that
needs a real model and is what `evals/` is for. Being clear about the boundary matters:
green tests here say the machine is wired correctly, not that the judgment is good.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import PrivateAttr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meridian.core import db as db_mod  # noqa: E402
from meridian.core.errors import MeridianError  # noqa: E402
from meridian.crews._base import citations_must_resolve  # noqa: E402
from meridian.crews.llm import METER, MeteredLLM  # noqa: E402
from meridian.models import Decision, IncomeType, LoanState  # noqa: E402
from meridian.vendors.fixtures import by_number  # noqa: E402


class ScriptedLLM(MeteredLLM):
    """Returns a canned response per task name. No network, no cassette."""

    _script: dict = PrivateAttr(default_factory=dict)
    _calls: list = PrivateAttr(default_factory=list)

    def __init__(self, script: dict[str, str]):
        super().__init__(model="gpt-4o-mini", mode="live")
        self._script = script
        self._calls = []

    def with_model(self, model: str):  # type: ignore[override]
        """Stay scripted when an agent asks for a different model tier.

        Without this the inherited implementation returns a real `MeteredLLM`, and the
        strong-tier agents quietly start making live network calls from inside the test
        suite — which is how this suite went from 20 seconds to hanging. A test double must
        refuse to be cloned into the real thing.
        """
        return self

    def call(self, messages, **kwargs):  # type: ignore[override]
        from meridian.crews.llm import _task_name

        task = _task_name(kwargs.get("from_task"))
        self._calls.append(task)
        for key, response in self._script.items():
            if key in task:
                return response
        raise AssertionError(f"ScriptedLLM has no response for task {task!r}")


SCRIPT = {
    "judge_citation_entailment": json.dumps(
        {"entailed": True, "rationale": "The section directly supports the claim."}
    ),
    "detect_application_completeness": json.dumps(
        {
            "name_present": True,
            "income_present": True,
            "ssn_present": True,
            "property_address_present": True,
            "estimated_value_present": True,
            "loan_amount_present": True,
            "income_type": "w2",
            "document_types": ["application", "w2", "paystub"],
            "narrative": "All six pieces of information are present.",
        }
    ),
    "analyze_income": json.dumps(
        {
            "monthly_qualifying_income": 12125.0,
            "method": "w2_salaried",
            "delegated_to_specialist": False,
            "interpretation": [],
            "narrative": "Base salary plus averaged bonus.",
        }
    ),
    "interpret_credit": json.dumps(
        {"flags": [], "narrative": "Representative score 762, middle of three."}
    ),
    "decide_collateral": json.dumps(
        {
            "exercise_value_acceptance": True,
            "rationale": "No OV-VA-01 disqualifier is present.",
            "blockers": [],
            "citations": [
                {
                    "corpus": "overlays",
                    "section": "OV-VA-01",
                    "claim": "an offer of value acceptance must not be exercised where the property is rural, unique, a second home or investment property, or in a declared disaster area",
                    "applied_to": "none of those factors is present on this file",
                }
            ],
        }
    ),
    "research_guidelines": json.dumps(
        {
            "findings": [
                {
                    "question": "What is the maximum DTI?",
                    "answer": "The overlay caps total debt-to-income at 45 percent for a salaried borrower.",
                    "citations": ["overlays:OV-DTI-01"],
                    "sufficient": True,
                    "rounds_used": 1,
                }
            ]
        }
    ),
    "underwrite_file": json.dumps(
        {
            "decision": "approved",
            "rationale": "DU returned Approve/Eligible and the file clears every overlay.",
            "conditions": [
                {
                    "kind": "prior_to_funding",
                    "description": "Obtain a verbal verification of employment.",
                    "citation": "fannie:B3-3.1-02",
                }
            ],
            "citations": [
                {
                    "corpus": "overlays",
                    "section": "OV-DTI-01",
                    "claim": "overlay caps the maximum total debt-to-income ratio at 45 percent for a salaried borrower",
                    "applied_to": "file is at 30.91 percent",
                }
            ],
            "overlay_conflicts": [],
            "adverse_action_reasons": [],
        }
    ),
    "qc_review": json.dumps({"findings": [], "passed": True}),
}


@pytest.fixture()
def db(tmp_path):
    conn = db_mod.Database(tmp_path / "t.db")
    db_mod.set_db(conn)
    yield conn
    conn.close()
    db_mod.set_db(None)
    METER.reset()


# -- guardrail -----------------------------------------------------------


class _Out:
    def __init__(self, raw: str):
        self.raw = raw
        self.pydantic = None
        self.json_dict = None


def test_guardrail_rejects_a_fabricated_section():
    ok, message = citations_must_resolve(_Out("Per overlays:OV-DTI-99 the cap is 43 percent."))
    assert ok is False
    assert "OV-DTI-99" in message and "do not exist" in message.lower()


def test_guardrail_rejects_output_with_no_citation_at_all():
    ok, message = citations_must_resolve(_Out("The borrower qualifies comfortably."))
    assert ok is False
    assert "corpus:SECTION" in message


def test_guardrail_accepts_a_real_section():
    ok, _ = citations_must_resolve(_Out("Per overlays:OV-DTI-02 the self-employed cap is 43 percent."))
    assert ok is True


# -- crew wiring ---------------------------------------------------------


def test_every_crew_constructs_from_yaml():
    from meridian.crews.governance.crew import GovernanceCrew
    from meridian.crews.intake.crew import IntakeCrew
    from meridian.crews.underwrite.crew import UnderwriteCrew
    from meridian.crews.verify.crew import VerifyCrew

    llm = ScriptedLLM(SCRIPT)
    assert sorted(IntakeCrew(llm).agents_cfg) == ["intake_agent"]
    assert sorted(VerifyCrew(llm).agents_cfg) == [
        "collateral_agent",
        "credit_liability_agent",
        "income_analyst_agent",
        "self_employed_specialist_agent",
    ]
    assert sorted(UnderwriteCrew(llm).agents_cfg) == [
        "guideline_research_agent",
        "underwriter_agent",
    ]
    assert sorted(GovernanceCrew(llm).agents_cfg) == ["compliance_qc_agent"]


def test_eight_agents_across_four_crews():
    from meridian.crews.governance.crew import GovernanceCrew
    from meridian.crews.intake.crew import IntakeCrew
    from meridian.crews.underwrite.crew import UnderwriteCrew
    from meridian.crews.verify.crew import VerifyCrew

    llm = ScriptedLLM(SCRIPT)
    total = sum(
        len(C(llm).agents_cfg) for C in (IntakeCrew, VerifyCrew, UnderwriteCrew, GovernanceCrew)
    )
    assert total == 8


def test_rosters_name_three_production_and_eight_demo_agents():
    from meridian.crews import CrewJudgment

    assert len(CrewJudgment(roster="production").active_agents) == 3
    assert len(CrewJudgment(roster="demo").active_agents) == 8
    assert set(CrewJudgment(roster="production").active_agents) == {
        "collateral_agent",
        "underwriter_agent",
        "compliance_qc_agent",
    }


def test_only_the_income_analyst_may_delegate():
    from meridian.crews.verify.crew import VerifyCrew

    cfg = VerifyCrew(ScriptedLLM(SCRIPT)).agents_cfg
    delegators = [name for name, spec in cfg.items() if spec.get("allow_delegation")]
    assert delegators == ["income_analyst_agent"]


def test_conditional_menu_omits_the_wrong_calculator():
    """The agent is not being trusted to avoid the self-employed calculator on a W-2
    file — it cannot reach it."""
    from meridian.tools import tools_for_income_analyst

    w2 = tools_for_income_analyst("w2")
    se = tools_for_income_analyst("self_employed")
    assert "calc_self_employed_income" not in w2
    assert "calc_w2_income" not in se
    assert "calc_w2_income" in w2 and "calc_self_employed_income" in se


def test_irreversible_tools_cannot_enter_any_agent_menu():
    from meridian.crews._base import as_tool
    from meridian.tools import IRREVERSIBLE

    for name in IRREVERSIBLE:
        with pytest.raises(ValueError, match="irreversible"):
            as_tool(name, "must remain Flow-owned", lambda: None)


def test_all_constructed_agent_menus_exclude_irreversible_tools():
    from meridian.crews.governance.crew import GovernanceCrew
    from meridian.crews.intake.crew import IntakeCrew
    from meridian.crews.underwrite.crew import UnderwriteCrew
    from meridian.crews.verify.crew import VerifyCrew
    from meridian.tools import IRREVERSIBLE

    llm = ScriptedLLM(SCRIPT)
    crews = [
        IntakeCrew(llm).build(),
        VerifyCrew(llm).build_income("w2"),
        VerifyCrew(llm).build_credit(),
        VerifyCrew(llm).build_collateral(),
        UnderwriteCrew(llm).build_research(),
        UnderwriteCrew(llm).build_underwrite(),
        GovernanceCrew(llm).build_qc(),
    ]
    for crew in crews:
        for agent in crew.agents:
            names = {getattr(tool, "name", "") for tool in agent.tools}
            assert not names & IRREVERSIBLE


# -- CrewJudgment drives the same Flow -----------------------------------


def test_crew_judgment_runs_the_whole_flow(db):
    """The point of the judgment seam: swap the judgment, keep the spine."""
    from meridian.crews import CrewJudgment
    from meridian.flow import OriginationFlow

    scenario = by_number(1)
    with db.transaction():
        db.save_loan_state(scenario["loan_id"], "new", {}, scenario=scenario["name"])

    judgment = CrewJudgment(mode="live")
    judgment._llm = ScriptedLLM(SCRIPT)
    judgment.bind = lambda scenario_name: None  # keep the scripted LLM in place

    flow = OriginationFlow(loan_id=scenario["loan_id"], judgment=judgment, mode="live")
    flow.kickoff()
    st = flow.state

    assert st.decision == Decision.APPROVED
    assert st.income_type == IncomeType.W2
    assert st.value_acceptance_exercised is True
    assert "order_appraisal" not in st.tool_calls, "value acceptance was exercised — no $600"
    assert st.qc_passed is True
    assert all(c.verified for c in st.citations), [c.qc_note for c in st.citations if not c.verified]
    assert not {
        "detect_application_completeness",
        "analyze_income",
        "interpret_credit",
        "research_guidelines",
    } & set(judgment._llm._calls)
    assert {"decide_collateral", "underwrite_file", "qc_review"} <= set(judgment._llm._calls)


def test_crew_and_stub_agree_on_the_scenario_1_decision(db):
    """If the two judgments disagree, the judgment changed. If both are wrong, the
    orchestration is. That is the diagnostic this seam buys."""
    from meridian.crews import CrewJudgment
    from meridian.flow import OriginationFlow
    from meridian.judgment import StubJudgment

    scenario = by_number(1)
    decisions = []
    for judgment in (StubJudgment(), CrewJudgment(mode="live")):
        db_mod.reset_db(Path(db.path).parent / f"{judgment.name}.db")
        conn = db_mod.get_db()
        with conn.transaction():
            conn.save_loan_state(scenario["loan_id"], "new", {}, scenario=scenario["name"])
        if judgment.name == "crew":
            judgment._llm = ScriptedLLM(SCRIPT)
            judgment.bind = lambda s: None
        flow = OriginationFlow(loan_id=scenario["loan_id"], judgment=judgment, mode="live")
        flow.kickoff()
        decisions.append(flow.state.decision)
    assert decisions[0] == decisions[1] == Decision.APPROVED


# -- the deterministic QC floor ------------------------------------------


def test_qc_floor_catches_what_the_agent_missed(db):
    """The agent reports a clean review. The Python floor must still catch a
    prohibited-basis term and a citation that does not verify — a control you can only
    rely on when the model cooperates is not a control."""
    from meridian.crews import CrewJudgment
    from meridian.models import Citation

    st = LoanState(loan_id="L9", decision=Decision.DENIED)
    st.decision_rationale = "Denied because the applicant's marital status is unstable."
    st.adverse_action_reasons = []
    st.citations = [
        Citation(corpus="overlays", section="OV-DTI-02", claim="the cap is 47 percent"),
        Citation(corpus="overlays", section="OV-NOPE-01", claim="anything at all"),
    ]

    judgment = CrewJudgment(mode="live")
    judgment._llm = ScriptedLLM(SCRIPT)  # qc_review returns findings=[], passed=True
    judgment.bind = lambda s: None
    result = judgment.qc_review(st)

    kinds = {f["kind"] for f in result["findings"]}
    assert "prohibited_basis" in kinds
    assert "citation_not_verified" in kinds
    assert "adverse_action_missing_reasons" in kinds
    assert "adverse_action_due" in kinds
    assert result["passed"] is False, "critical findings must fail the review"
    assert result["citation_validity_rate"] == 0.0


def test_entailment_judge_catches_logical_inversion_after_lexical_check(db):
    from meridian.crews import CrewJudgment
    from meridian.models import Citation

    st = LoanState(loan_id="L10", decision=Decision.APPROVED)
    st.citations = [
        Citation(
            corpus="fannie",
            section="B2-1.2-01",
            claim=(
                "loan-to-value on a purchase transaction is calculated using the greater "
                "of the sales price or the appraised value"
            ),
        )
    ]
    script = dict(SCRIPT)
    script["judge_citation_entailment"] = json.dumps(
        {
            "entailed": False,
            "rationale": "The section requires the lesser value; the claim reverses it.",
        }
    )
    judgment = CrewJudgment(mode="live")
    judgment._llm = ScriptedLLM(script)
    judgment.bind = lambda s: None

    result = judgment.qc_review(st)

    assert st.citations[0].supports_claim is True, "the lexical floor alone accepts this"
    assert st.citations[0].entailed is False
    assert st.citations[0].verified is False
    assert any(f["kind"] == "citation_not_verified" for f in result["findings"])


@pytest.mark.parametrize("gate", ["G1", "G2", "G3", "G4"])
def test_every_human_gate_persists_a_named_queue_item(db, gate):
    from meridian.core.errors import ApprovalRequired
    from meridian.flow import OriginationFlow
    from meridian.judgment import StubJudgment

    loan_id = f"APPROVAL-{gate}"
    flow = OriginationFlow(loan_id=loan_id, judgment=StubJudgment())
    with pytest.raises(ApprovalRequired, match=gate):
        flow._require_approval(gate, {"kind": "test_artifact"})

    queued = db.approval(loan_id, gate)
    assert queued and queued["status"] == "pending"
    assert queued["artifact"] == {"kind": "test_artifact"}
    assert db.load_loan_state(loan_id)["status"] == f"awaiting_approval_{gate.lower()}"


def test_approval_pause_is_marked_as_expected_flow_control():
    from meridian.core.errors import ApprovalRequired

    exc = ApprovalRequired("G2", "MER-1003")

    assert exc._flow_listener_logged is True


def test_approval_survives_process_restart_and_resumes_from_ledger(db):
    from meridian.core.errors import ApprovalRequired
    from meridian.flow import OriginationFlow
    from meridian.judgment import StubJudgment

    scenario = by_number(2)  # reaches the documented OV-DTI-02 exception gate
    with db.transaction():
        db.save_loan_state(scenario["loan_id"], "new", {}, scenario=scenario["name"])

    with pytest.raises(ApprovalRequired, match="G2"):
        OriginationFlow(scenario["loan_id"], StubJudgment()).kickoff()
    assert db.vendor_call_count("credit_bureau", "tri_merge", scenario["loan_id"]) == 1

    # A fresh Flow instance still sees the same open gate, and saga replay prevents a
    # second hard inquiry while it reconstructs the path to that boundary.
    with pytest.raises(ApprovalRequired, match="G2"):
        OriginationFlow(scenario["loan_id"], StubJudgment()).kickoff()
    assert db.vendor_call_count("credit_bureau", "tri_merge", scenario["loan_id"]) == 1

    queued = db.approval(scenario["loan_id"], "G2")
    assert queued
    digest = queued["artifact_digest"]
    db.record_approval(
        scenario["loan_id"], "G2", digest, "approved", "Avery Underwriter"
    )
    with pytest.raises(MeridianError, match="no pending approval"):
        db.record_approval(
            scenario["loan_id"], "G2", digest, "rejected", "Morgan Reviewer"
        )
    recorded = db.approval(scenario["loan_id"], "G2")
    assert recorded and recorded["status"] == "approved"
    assert recorded["approver"] == "Avery Underwriter"

    resumed = OriginationFlow(scenario["loan_id"], StubJudgment())
    resumed.kickoff()

    assert resumed.state.pending_approval is None
    assert resumed.state.approvals[0]["approver"] == "Avery Underwriter"
    assert db.load_loan_state(scenario["loan_id"])["status"] == "decided"
    assert db.vendor_call_count("credit_bureau", "tri_merge", scenario["loan_id"]) == 1
