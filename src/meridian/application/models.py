"""Typed application DTOs.

These models are deliberately independent of any UI framework or HTTP transport. They
are the stable presentation boundary the FastAPI adapter and the CLI both build on.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from meridian.models import Decision


class ScenarioOption(BaseModel):
    scenario_id: int
    label: str
    name: str
    loan_id: str
    expected_decision: str | None = None
    summary: str | None = None


class RunRequest(BaseModel):
    scenario_id: int
    judgment_kind: Literal["stub", "crew"] = "stub"
    roster: Literal["production", "demo"] = "production"
    policy_attack: bool = False
    submit_twice: bool = False
    auto_approve: bool = False


class EventRecord(BaseModel):
    kind: str
    actor: str
    loan_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float
    line: str


class OverlayConflictView(BaseModel):
    dimension: str
    actual: Any = None
    agency: Any = None
    overlay: Any = None
    citation: str | None = None
    exception_granted: bool = False


class ValueAcceptanceView(BaseModel):
    offered: bool = False
    exercised: bool | None = None
    rationale: str = ""


class ConditionView(BaseModel):
    kind: str
    description: str
    citation: str | None = None
    raised_by: str


class CitationView(BaseModel):
    corpus: str
    section: str
    claim: str
    applied_to: str | None = None
    quoted: str | None = None
    exists: bool | None = None
    supports_claim: bool | None = None
    entailed: bool | None = None
    verified: bool | None = None
    qc_note: str | None = None


class FindingView(BaseModel):
    severity: str
    kind: str
    detail: str


class GuidelineFindingView(BaseModel):
    question: str
    rounds_used: int | None = None
    sufficient: bool | None = None
    citations: list[str] = Field(default_factory=list)


class LedgerEntryView(BaseModel):
    id: int
    step: str
    phase: str
    outcome: str
    idempotency_key: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: float


class CompensationView(BaseModel):
    step: str
    outcome: str
    note: str
    detail: dict[str, Any] = Field(default_factory=dict)


class NoticeView(BaseModel):
    kind: str
    issued_on: str
    due_on: str
    citation: str | None = None
    reasons: list[str] = Field(default_factory=list)


class CycleTimeView(BaseModel):
    scope: str | None = None
    basis: str | None = None
    baseline_days: float | None = None
    baseline_touch_minutes: float | None = None
    modeled_days: float | None = None
    irreducible_floor_days: float | None = None
    baseline_underwriter_touches: int | None = None
    modeled_underwriter_touches: int | None = None
    measured_agent_seconds: float | None = None
    does_not_compress: list[str] = Field(default_factory=list)


class AgentRunView(BaseModel):
    agent: str
    task: str
    seconds: float
    prompt_tokens: int
    completion_tokens: int
    usd: float
    delegated_to: str | None = None
    tools_called: list[str] = Field(default_factory=list)
    replayed: bool = False


class IdempotencyView(BaseModel):
    enabled: bool = False
    calls_before_second_run: int | None = None
    calls_after_second_run: int | None = None


class LoanRunResult(BaseModel):
    loan_id: str
    scenario_id: int | None = None
    scenario_name: str
    decision: Decision | None = None
    decision_rationale: str = ""
    expected_decision: str | None = None
    expected_matches: bool | None = None
    overlays: list[OverlayConflictView] = Field(default_factory=list)
    value_acceptance: ValueAcceptanceView = Field(default_factory=ValueAcceptanceView)
    conditions: list[ConditionView] = Field(default_factory=list)
    citations: list[CitationView] = Field(default_factory=list)
    qc_findings: list[FindingView] = Field(default_factory=list)
    controls_fired: list[str] = Field(default_factory=list)
    guideline_findings: list[GuidelineFindingView] = Field(default_factory=list)
    ledger: list[LedgerEntryView] = Field(default_factory=list)
    compensation: list[CompensationView] = Field(default_factory=list)
    notices: list[NoticeView] = Field(default_factory=list)
    cycle_time: CycleTimeView | None = None
    agent_runs: list[AgentRunView] = Field(default_factory=list)
    tri_merge_calls: int = 0
    idempotency: IdempotencyView = Field(default_factory=IdempotencyView)


class ApprovalItem(BaseModel):
    loan_id: str
    gate: str
    status: str
    approver: str | None = None
    artifact_digest: str
    artifact: dict[str, Any]
    requested_at: float
    decided_at: float | None = None


class ApprovalDecisionRequest(BaseModel):
    loan_id: str
    gate: str
    artifact_digest: str
    decision: Literal["approved", "rejected"]
    approver: str


RunStatus = Literal["running", "completed", "awaiting_approval", "failed"]


class RunUpdate(BaseModel):
    status: RunStatus
    scenario: ScenarioOption
    mode: str
    events: list[EventRecord] = Field(default_factory=list)
    trace_lines: list[str] = Field(default_factory=list)
    result: LoanRunResult | None = None
    approval: ApprovalItem | None = None
    error_type: str | None = None
    error_message: str | None = None

    def trace_text(self) -> str:
        return "\n".join(self.trace_lines)


class EvaluationReport(BaseModel):
    exists: bool
    markdown: str
    path: str
    modified_at: float | None = None


class EvaluationRunResult(BaseModel):
    returncode: int
    output: str
    report: EvaluationReport
