"""Map authoritative domain state and ledger rows into typed UI DTOs."""

from __future__ import annotations

import json
from typing import Any

from meridian.core.db import Database
from meridian.core.events import Event
from meridian.models import LoanState
from meridian.vendors.fixtures import by_number

from .models import (
    AgentRunView,
    ApprovalItem,
    CitationView,
    CompensationView,
    ConditionView,
    CycleTimeView,
    EventRecord,
    FindingView,
    GuidelineFindingView,
    IdempotencyView,
    LedgerEntryView,
    LoanRunResult,
    NoticeView,
    OverlayConflictView,
    ValueAcceptanceView,
)


def present_event(event: Event) -> EventRecord:
    return EventRecord(
        kind=event.kind,
        actor=event.actor,
        loan_id=event.loan_id,
        payload=event.payload,
        timestamp=event.ts,
        line=event.line(),
    )


def present_approval(row: dict[str, Any]) -> ApprovalItem:
    return ApprovalItem.model_validate(row)


def present_loan_state(
    state: LoanState,
    db: Database,
    *,
    idempotency: IdempotencyView | None = None,
) -> LoanRunResult:
    expected = None
    if state.scenario_id:
        scenario = by_number(state.scenario_id) or {}
        expected = (scenario.get("expected") or {}).get("decision")

    overlays = [
        OverlayConflictView(
            dimension=str(item.get("dimension", "")),
            actual=item.get("actual"),
            agency=item.get("agency"),
            overlay=item.get("overlay"),
            citation=item.get("citation"),
            exception_granted=bool(item.get("exception_granted")),
        )
        for item in state.overlay_conflicts
    ]
    findings = [
        FindingView(
            severity=str(item.get("severity", "info")),
            kind=str(item.get("kind", "finding")),
            detail=str(item.get("detail", "")),
        )
        for item in state.qc_findings
    ]
    guidelines = [
        GuidelineFindingView(
            question=str(item.get("question", "")),
            rounds_used=item.get("rounds_used"),
            sufficient=item.get("sufficient"),
            citations=[str(value) for value in item.get("citations") or []],
        )
        for item in state.guideline_findings
    ]
    ledger = [
        LedgerEntryView(
            id=int(row["id"]),
            step=str(row["step"]),
            phase=str(row["phase"]),
            outcome=str(row["outcome"]),
            idempotency_key=row.get("key"),
            detail=row.get("detail") or {},
            timestamp=float(row["ts"]),
        )
        for row in db.saga_history(state.loan_id)
    ]
    notices = [
        NoticeView(
            kind=str(row["kind"]),
            issued_on=str(row["issued_on"]),
            due_on=str(row["due_on"]),
            citation=row["citation"],
            reasons=[str(reason) for reason in json.loads(row["reasons_json"] or "[]")],
        )
        for row in db.query("SELECT * FROM notices WHERE loan_id = ?", (state.loan_id,))
    ]
    cycle_time = CycleTimeView.model_validate(state.cycle_time) if state.cycle_time else None

    return LoanRunResult(
        loan_id=state.loan_id,
        scenario_id=state.scenario_id,
        scenario_name=state.scenario_name,
        decision=state.decision,
        decision_rationale=state.decision_rationale,
        expected_decision=expected,
        expected_matches=(
            expected == state.decision.value if expected is not None and state.decision is not None else None
        ),
        overlays=overlays,
        value_acceptance=ValueAcceptanceView(
            offered=state.value_acceptance_offered,
            exercised=state.value_acceptance_exercised,
            rationale=state.collateral_rationale,
        ),
        conditions=[
            ConditionView(
                kind=condition.kind.value,
                description=condition.description,
                citation=condition.citation,
                raised_by=condition.raised_by,
            )
            for condition in state.conditions
        ],
        citations=[CitationView.model_validate(citation.model_dump()) for citation in state.citations],
        qc_findings=findings,
        controls_fired=list(state.errors),
        guideline_findings=guidelines,
        ledger=ledger,
        compensation=[
            CompensationView(
                step=str(row.get("step", "")),
                outcome=str(row.get("outcome", "")),
                note=str(row.get("note", "")),
                detail=row.get("detail") or {},
            )
            for row in state.saga_report
        ],
        notices=notices,
        cycle_time=cycle_time,
        agent_runs=[AgentRunView.model_validate(run.model_dump()) for run in state.agent_runs],
        tri_merge_calls=db.vendor_call_count("credit_bureau", "tri_merge", state.loan_id),
        idempotency=idempotency or IdempotencyView(),
    )
