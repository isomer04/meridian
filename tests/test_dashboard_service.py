"""Characterization and service tests for the dashboard application boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.application import (
    ApprovalDecisionRequest,
    RunRequest,
    approval_queue,
    decide_approval,
    load_evaluation_report,
    run_evaluations,
    run_loan,
    scenario_label_map,
    scenario_options,
)
from meridian.core import db as db_mod
from meridian.core.errors import MeridianError
from meridian.core.events import BUS
from meridian.models import Decision


@pytest.fixture()
def db(tmp_path):
    database = db_mod.Database(tmp_path / "dashboard.db")
    db_mod.set_db(database)
    BUS.clear()
    yield database
    database.close()
    db_mod.set_db(None)
    BUS.clear()


def test_scenario_options_are_stable_and_addressable():
    options = scenario_options()
    labels = scenario_label_map()

    assert [item.scenario_id for item in options] == sorted(item.scenario_id for item in options)
    assert len({item.scenario_id for item in options}) == len(options)
    assert labels == {item.label: item.scenario_id for item in options}
    assert options[0].loan_id == "MER-1001"
    assert options[0].expected_decision == "approved"


def test_completed_run_exposes_structured_determination(db):
    updates = list(run_loan(RunRequest(scenario_id=1, judgment_kind="stub")))

    assert [update.status for update in updates] == ["running", "completed"]
    completed = updates[-1]
    result = completed.result
    assert result is not None
    assert result.decision == Decision.APPROVED
    assert result.expected_matches is True
    assert result.value_acceptance.exercised is True
    assert result.compensation == []
    assert result.citations and all(citation.verified for citation in result.citations)
    assert result.ledger
    assert completed.events
    assert "pull_credit" in completed.trace_text()


def test_denial_pauses_for_named_approval_then_resumes(db):
    paused_updates = list(run_loan(RunRequest(scenario_id=3, judgment_kind="stub")))

    assert [update.status for update in paused_updates] == ["running", "awaiting_approval"]
    paused = paused_updates[-1]
    assert paused.approval is not None
    assert paused.approval.loan_id == "MER-1003"
    assert paused.approval.gate == "G1"
    assert paused.approval.status == "pending"
    assert "HUMAN APPROVAL REQUIRED" in paused.trace_text()

    decide_approval(
        ApprovalDecisionRequest(
            loan_id=paused.approval.loan_id,
            gate=paused.approval.gate,
            artifact_digest=paused.approval.artifact_digest,
            decision="approved",
            approver="Avery Underwriter",
        )
    )
    resumed = list(run_loan(RunRequest(scenario_id=3, judgment_kind="stub")))[-1]

    assert resumed.status == "completed"
    assert resumed.result is not None
    assert resumed.result.decision == Decision.DENIED
    assert resumed.result.expected_matches is True
    assert any(item.citation == "overlays:OV-LTV-03" for item in resumed.result.overlays)
    assert [item.step for item in resumed.result.compensation[:2]] == [
        "lock_rate",
        "order_appraisal",
    ]
    assert any(notice.kind == "adverse_action" for notice in resumed.result.notices)


def test_duplicate_submission_records_idempotency_evidence(db):
    completed = list(
        run_loan(
            RunRequest(
                scenario_id=4,
                judgment_kind="stub",
                submit_twice=True,
                auto_approve=True,
            )
        )
    )[-1]

    assert completed.status == "completed"
    assert completed.result is not None
    evidence = completed.result.idempotency
    assert evidence.enabled is True
    assert evidence.calls_before_second_run == 1
    assert evidence.calls_after_second_run == 1
    assert "SECOND SUBMISSION" in completed.trace_text()
    assert "replayed" in completed.trace_text()


def test_run_failure_is_a_typed_terminal_state(db, monkeypatch):
    from meridian.application import dashboard

    class BrokenFlow:
        def __init__(self, **_kwargs):
            pass

        def kickoff(self):
            raise RuntimeError("controlled test failure")

    monkeypatch.setattr("meridian.flow.OriginationFlow", BrokenFlow)
    updates = list(dashboard.run_loan(RunRequest(scenario_id=1, judgment_kind="stub")))

    assert [update.status for update in updates] == ["running", "failed"]
    assert updates[-1].error_type == "RuntimeError"
    assert updates[-1].error_message == "controlled test failure"
    assert "RUN FAILED" in updates[-1].trace_text()


def test_approval_service_preserves_digest_and_named_reviewer_guards(db):
    db.request_approval("L-APPROVAL", "G2", {"kind": "overlay_exception", "version": 1})
    item = approval_queue()[0]

    with pytest.raises(ValueError, match="named approver"):
        decide_approval(
            ApprovalDecisionRequest(
                loan_id=item.loan_id,
                gate=item.gate,
                artifact_digest=item.artifact_digest,
                decision="approved",
                approver="   ",
            )
        )
    with pytest.raises(MeridianError, match="no pending approval"):
        decide_approval(
            ApprovalDecisionRequest(
                loan_id=item.loan_id,
                gate=item.gate,
                artifact_digest="stale-digest",
                decision="approved",
                approver="Morgan Reviewer",
            )
        )

    decide_approval(
        ApprovalDecisionRequest(
            loan_id=item.loan_id,
            gate=item.gate,
            artifact_digest=item.artifact_digest,
            decision="rejected",
            approver="Morgan Reviewer",
        )
    )
    assert approval_queue() == []
    recorded = db.approval(item.loan_id, item.gate)
    assert recorded and recorded["status"] == "rejected"
    assert recorded["approver"] == "Morgan Reviewer"


def test_evaluation_report_has_explicit_missing_and_present_states(tmp_path):
    report_path = tmp_path / "report.md"
    missing = load_evaluation_report(report_path)
    assert missing.exists is False
    assert "No evaluation report" in missing.markdown

    report_path.write_text("# Evaluation report\n\nMeasured evidence.", encoding="utf-8")
    present = load_evaluation_report(report_path)
    assert present.exists is True
    assert present.markdown.startswith("# Evaluation report")
    assert present.modified_at is not None


def test_evaluation_runner_returns_bounded_output_and_updated_report(tmp_path):
    script = tmp_path / "run_eval.py"
    report = tmp_path / "report.md"
    script.write_text(
        "from pathlib import Path\n"
        "print('decision accuracy 10/10')\n"
        "Path('report.md').write_text('# Fresh report', encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = run_evaluations(script=script, report_path=report, cwd=tmp_path)

    assert result.returncode == 0
    assert "decision accuracy 10/10" in result.output
    assert result.report.exists is True
    assert result.report.markdown == "# Fresh report"
