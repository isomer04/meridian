"""Typed agent outputs.

Every crew task declares `output_pydantic` against one of these. That is not tidiness —
it is what keeps the routers deterministic. A `@router` must branch on a typed field, so
the agent has to *return* a typed field. The moment a router parses prose to find the
path, the model owns control flow and the whole thesis is gone.

Note what these models deliberately do **not** contain: computed figures. The underwriter
returns a decision and a rationale, not a DTI. The DTI arrives from `calc/dti.py` as
input. The model may choose the path; it may never choose a number.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IntakeOutput(BaseModel):
    name_present: bool = Field(description="borrower name is on file")
    income_present: bool
    ssn_present: bool
    property_address_present: bool
    estimated_value_present: bool
    loan_amount_present: bool
    income_type: str = Field(description="one of: w2, self_employed, mixed")
    document_types: list[str] = Field(default_factory=list)
    narrative: str


class IncomeOutput(BaseModel):
    monthly_qualifying_income: float = Field(
        description="MUST be copied from the calculator tool's output — do not compute it yourself"
    )
    method: str
    delegated_to_specialist: bool = Field(
        description="true if the self-employed specialist performed the analysis"
    )
    interpretation: list[str] = Field(
        default_factory=list, description="judgment calls and items needing documentation"
    )
    narrative: str


class CreditOutput(BaseModel):
    flags: list[str] = Field(default_factory=list)
    narrative: str


class CitedClaim(BaseModel):
    corpus: str = Field(description="fannie, fha or overlays")
    section: str = Field(description="the section identifier, e.g. OV-DTI-02")
    claim: str = Field(
        description="what the GUIDELINE says. Not what this file's figures are — that goes in applied_to."
    )
    applied_to: str = Field(default="", description="this file's figures, which the corpus does not contain")


class CollateralOutput(BaseModel):
    exercise_value_acceptance: bool = Field(
        description="true to accept DU's offer, false to order a full appraisal anyway"
    )
    rationale: str = Field(description="must name the specific OV-VA-01 factors considered")
    blockers: list[str] = Field(default_factory=list)
    citations: list[CitedClaim] = Field(
        default_factory=list,
        description="each citation must state what the guideline says in `claim`, separately from this file's facts",
    )


class ResearchFinding(BaseModel):
    question: str
    answer: str
    citations: list[str] = Field(description="corpus:SECTION references that support the answer")
    sufficient: bool
    rounds_used: int = 1


class ResearchOutput(BaseModel):
    findings: list[ResearchFinding]


class OverlayConflict(BaseModel):
    dimension: str
    agency: str
    overlay: str
    actual: str
    binds: str = "overlay"
    citation: str
    exception_granted: bool = False


class ConditionOut(BaseModel):
    kind: str = Field(description="prior_to_docs, prior_to_funding, prior_to_closing or at_close")
    description: str
    citation: str | None = None


class UnderwriteOutput(BaseModel):
    decision: str = Field(
        description="approved, approved_with_conditions, suspended or denied"
    )
    rationale: str
    conditions: list[ConditionOut] = Field(default_factory=list)
    citations: list[CitedClaim] = Field(default_factory=list)
    overlay_conflicts: list[OverlayConflict] = Field(default_factory=list)
    adverse_action_reasons: list[str] = Field(
        default_factory=list,
        description="required and non-empty when denied; must state the specific principal reasons actually relied upon, and must never reference a prohibited basis",
    )


class QCFinding(BaseModel):
    severity: str = Field(description="critical, high, medium or info")
    kind: str
    detail: str


class QCOutput(BaseModel):
    findings: list[QCFinding] = Field(default_factory=list)
    passed: bool


class EntailmentOutput(BaseModel):
    entailed: bool = Field(
        description="true only when the cited section logically supports the asserted claim"
    )
    rationale: str = Field(description="briefly identify the supporting or contradicting logic")
