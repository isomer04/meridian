"""HTTP request and response models for the Phase 2 tracer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from meridian.application.models import ApprovalItem, LoanRunResult, ScenarioOption


class ErrorResponse(BaseModel):
    code: str
    message: str
    active_run_id: str | None = None
    active_evaluation_id: str | None = None


class ErrorEnvelope(BaseModel):
    detail: ErrorResponse


class SystemResponse(BaseModel):
    version: str
    api_key_present: bool
    default_judgment: Literal["stub", "crew"]
    active_run_id: str | None = None
    active_evaluation_id: str | None = None
    crew_available: bool
    crew_unavailable_reason: str | None = None
    crew_setup_action: str | None = None


class ScenarioListResponse(BaseModel):
    scenarios: list[ScenarioOption]


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: int = Field(ge=1)
    judgment_kind: Literal["stub", "crew"] = "stub"
    roster: Literal["production", "demo"] = "production"
    policy_attack: bool = False
    submit_twice: bool = False
    auto_approve: bool = False


class StartRunResponse(BaseModel):
    run_id: str
    status: Literal["running"]


class IntakeConfirmationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=1, pattern=r".*\S.*")
    expected_revision: int = Field(ge=1)
    case: dict[str, Any]


class IntakeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judgment_kind: Literal["stub", "crew"] = "stub"
    roster: Literal["production", "demo"] = "production"
    auto_approve: bool = False


class RunResponse(BaseModel):
    """A reconnectable snapshot consumed by the case workspace."""

    run_id: str
    status: Literal["running", "completed", "awaiting_approval", "failed"]
    mode: str | None = None
    scenario: ScenarioOption | None = None
    events: list["EventEnvelope"] = Field(default_factory=list)
    trace_lines: list[str] = Field(default_factory=list)
    result: LoanRunResult | None = None
    approval: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    terminal_at: str | None = None
    terminal_persistence_error: str | None = None


class EventEnvelope(BaseModel):
    id: int
    run_id: str
    type: str
    actor: str
    loan_id: str | None = None
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)
    line: str


class ApprovalQueueResponse(BaseModel):
    approvals: list[ApprovalItem]


class ApprovalDecisionBody(BaseModel):
    """`loan_id` and `gate` are path parameters; the body carries only the decision
    inputs plus the artifact digest the browser must echo back unchanged."""

    artifact_digest: str
    decision: Literal["approved", "rejected"]
    approver: str = Field(min_length=1, pattern=r".*\S.*")


class EvaluationReportResponse(BaseModel):
    exists: bool
    markdown: str
    path: str
    modified_at: float | None = None


class StartEvaluationResponse(BaseModel):
    evaluation_id: str
    status: Literal["running"]


class EvaluationStatusResponse(BaseModel):
    """A reconnectable snapshot consumed by the evaluation page."""

    evaluation_id: str
    status: Literal["running", "completed", "failed"]
    returncode: int | None = None
    output: str = ""
    report: EvaluationReportResponse | None = None
    error_message: str | None = None
    started_at: str
    terminal_at: str | None = None


class DocumentResponse(BaseModel):
    slug: Literal["architecture", "assumptions"]
    title: str
    markdown: str
