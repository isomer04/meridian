"""Human approval queue and decision routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from meridian.api.models import ApprovalDecisionBody, ApprovalQueueResponse, ErrorResponse
from meridian.application.dashboard import approval, approval_queue, decide_approval
from meridian.application.models import ApprovalDecisionRequest
from meridian.core.errors import MeridianError

router = APIRouter(tags=["approvals"])


@router.get("/approvals", response_model=ApprovalQueueResponse)
def get_approvals() -> ApprovalQueueResponse:
    return ApprovalQueueResponse(approvals=approval_queue())


@router.post(
    "/approvals/{loan_id}/{gate}/decision",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def post_approval_decision(loan_id: str, gate: str, body: ApprovalDecisionBody) -> None:
    if approval(loan_id, gate) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(code="approval_not_found", message="approval not found").model_dump(),
        )
    try:
        decide_approval(
            ApprovalDecisionRequest(
                loan_id=loan_id,
                gate=gate,
                artifact_digest=body.artifact_digest,
                decision=body.decision,
                approver=body.approver,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(code="invalid_decision", message=str(exc)).model_dump(),
        ) from exc
    except MeridianError as exc:
        # `Database.record_approval` raises when no *pending* row matches the given
        # digest — either the gate was already decided, or (the interesting case) the
        # artifact changed after the browser loaded the queue. Either way this is a
        # conflict the client must resolve by refreshing, not a client input error.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ErrorResponse(code="stale_approval", message=str(exc)).model_dump(),
        ) from exc
