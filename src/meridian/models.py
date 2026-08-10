"""Typed state. The `@router` methods read from these, and nothing else.

Deterministic control flow needs typed state to be deterministic *about*. If a router
branched on a string an agent produced, the model would be choosing the path, and the
whole claim collapses. So every field a router reads is written either by `calc/` or
by a vendor — never parsed out of a model's prose.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Decision(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_CONDITIONS = "approved_with_conditions"
    SUSPENDED = "suspended"
    DENIED = "denied"
    INCOMPLETE = "incomplete"


class ConditionType(str, Enum):
    """PTD / PTF / PTC / at-close. Four, not three — the missing one is usually PTC."""

    PTD = "prior_to_docs"
    PTF = "prior_to_funding"
    PTC = "prior_to_closing"
    AT_CLOSE = "at_close"


class IncomeType(str, Enum):
    W2 = "w2"
    SELF_EMPLOYED = "self_employed"
    MIXED = "mixed"


class Condition(BaseModel):
    kind: ConditionType
    description: str
    citation: str | None = None
    raised_by: str = "underwriter_agent"


class Citation(BaseModel):
    """A guideline reference an agent asserted, plus the QC verdict on it.

    `verified` is what the citation-validity metric aggregates. A hallucinated
    citation is a compliance event at a lender, so the check is a first-class field
    rather than a log line.

    `claim` and `applied_to` are deliberately separate fields. `claim` is the assertion
    about **what the guideline says** — that is what gets verified against the corpus.
    `applied_to` is **what this file's figures are**, which the corpus obviously does
    not contain. Collapsing the two produces a verifier that rejects
    *"OV-LTV-03 caps second homes at 80%, and this file is at 86.36%"* because 86.36
    does not appear in the guideline — a false positive that would make the whole
    metric meaningless.
    """

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


class SixPiecesOfInformation(BaseModel):
    """12 CFR 1026.2(a)(3) — what legally constitutes an *application*.

    Name, income, SSN, property address, estimated value, loan amount. Receipt of all
    six starts the 3-business-day Loan Estimate clock, and it is the honest start
    point for a cycle-time claim. Measuring from "borrower first called" would be
    flattering and wrong.
    """

    name: bool = False
    income: bool = False
    ssn: bool = False
    property_address: bool = False
    estimated_value: bool = False
    loan_amount: bool = False

    @property
    def complete(self) -> bool:
        return all(
            (self.name, self.income, self.ssn, self.property_address, self.estimated_value, self.loan_amount)
        )

    def missing(self) -> list[str]:
        return [k for k, v in self.model_dump().items() if not v]


class AgentRun(BaseModel):
    """Per-agent cost and latency. "Which agent is expensive?" is a real operating
    question and the numbers are free to collect."""

    agent: str
    task: str = ""
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    delegated_to: str | None = None
    tools_called: list[str] = Field(default_factory=list)
    replayed: bool = False


class LoanState(BaseModel):
    """The authoritative loan record.

    Mirrored into `loan_state` inside `transaction()` at every step boundary. CrewAI's
    flow state is a cache of this, not the other way round.
    """

    loan_id: str = ""
    scenario_id: int | None = None
    scenario_name: str = ""

    # -- application
    borrower: dict[str, Any] = Field(default_factory=dict)
    co_borrower: dict[str, Any] | None = None
    property: dict[str, Any] = Field(default_factory=dict)
    loan: dict[str, Any] = Field(default_factory=dict)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    six_pieces: SixPiecesOfInformation = Field(default_factory=SixPiecesOfInformation)
    application_complete_on: str | None = None

    # -- compliance facts the policy gate reads
    le_delivered: bool = False
    le_due_on: str | None = None
    intent_to_proceed: bool = False

    # -- verification
    income_type: IncomeType | None = None
    income_analysis: dict[str, Any] = Field(default_factory=dict)
    delegated_to_specialist: bool = False
    credit: dict[str, Any] = Field(default_factory=dict)
    dti: dict[str, Any] = Field(default_factory=dict)
    ltv: dict[str, Any] = Field(default_factory=dict)
    employment: dict[str, Any] = Field(default_factory=dict)

    # -- AUS / collateral
    aus_findings: dict[str, Any] = Field(default_factory=dict)
    value_acceptance_offered: bool = False
    value_acceptance_exercised: bool | None = None
    collateral_rationale: str = ""
    appraisal: dict[str, Any] = Field(default_factory=dict)
    lock: dict[str, Any] = Field(default_factory=dict)

    # -- underwriting
    guideline_findings: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    overlay_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    decision: Decision | None = None
    decision_rationale: str = ""
    conditions: list[Condition] = Field(default_factory=list)
    adverse_action_reasons: list[str] = Field(default_factory=list)

    # -- governance
    qc_findings: list[dict[str, Any]] = Field(default_factory=list)
    qc_passed: bool | None = None
    behavior_findings: list[dict[str, Any]] = Field(default_factory=list)
    pending_approval: dict[str, Any] | None = None
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    auto_approve: bool = False

    # -- run metadata
    agent_runs: list[AgentRun] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    saga_report: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    cycle_time: dict[str, Any] = Field(default_factory=dict)
    mode: str = "replay"

    def le_deadline(self, from_day: date | None = None) -> str:
        """3 business days from application-complete. Approximated as calendar days
        skipping weekends — good enough for a demo, and labelled as an approximation
        rather than presented as a compliance calendar."""
        d = from_day or date.today()
        added = 0
        while added < 3:
            d += timedelta(days=1)
            if d.weekday() < 5:
                added += 1
        return d.isoformat()

    def total_usd(self) -> float:
        return round(sum(r.usd for r in self.agent_runs), 4)

    def total_seconds(self) -> float:
        return round(sum(r.seconds for r in self.agent_runs), 2)


ADVERSE_ACTION_DAYS = 30  # ECOA / Reg B — 30 days, not 15 and not 60
