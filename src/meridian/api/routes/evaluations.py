"""Evaluation report and one-active-job harness routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from meridian.api.dependencies import get_evaluation_coordinator
from meridian.api.models import (
    ErrorEnvelope,
    ErrorResponse,
    EvaluationReportResponse,
    EvaluationStatusResponse,
    StartEvaluationResponse,
)
from meridian.application.evaluation_coordinator import (
    ActiveEvaluationError,
    CoordinatedEvaluation,
    EvaluationCoordinator,
    UnknownEvaluationError,
)
from meridian.application.evaluation_service import load_evaluation_report

router = APIRouter(tags=["evaluations"])


def _not_found(evaluation_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ErrorResponse(
            code="evaluation_not_found", message=f"evaluation {evaluation_id!r} was not found"
        ).model_dump(),
    )


def _status_response(evaluation: CoordinatedEvaluation) -> EvaluationStatusResponse:
    with evaluation.condition:
        return EvaluationStatusResponse(
            evaluation_id=evaluation.evaluation_id,
            status=evaluation.status,
            returncode=evaluation.returncode,
            output="\n".join(evaluation.output_lines),
            report=EvaluationReportResponse(**evaluation.report.model_dump()) if evaluation.report else None,
            error_message=evaluation.error_message,
            started_at=evaluation.started_at,
            terminal_at=evaluation.terminal_at,
        )


@router.get("/evaluation/report", response_model=EvaluationReportResponse)
def get_evaluation_report(
    coordinator: EvaluationCoordinator = Depends(get_evaluation_coordinator),
) -> EvaluationReportResponse:
    report = load_evaluation_report(coordinator.report_path)
    return EvaluationReportResponse(**report.model_dump())


@router.post(
    "/evaluations",
    response_model=StartEvaluationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"model": ErrorEnvelope}},
)
def start_evaluation(
    coordinator: EvaluationCoordinator = Depends(get_evaluation_coordinator),
) -> StartEvaluationResponse:
    try:
        evaluation = coordinator.start()
    except ActiveEvaluationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ErrorResponse(
                code="evaluation_active",
                message="another evaluation run is already active",
                active_evaluation_id=exc.evaluation_id,
            ).model_dump(),
        ) from exc
    return StartEvaluationResponse(evaluation_id=evaluation.evaluation_id, status="running")


@router.get(
    "/evaluations/{evaluation_id}",
    response_model=EvaluationStatusResponse,
    responses={404: {"model": ErrorEnvelope}},
)
def get_evaluation(
    evaluation_id: str,
    coordinator: EvaluationCoordinator = Depends(get_evaluation_coordinator),
) -> EvaluationStatusResponse:
    try:
        return _status_response(coordinator.get(evaluation_id))
    except UnknownEvaluationError as exc:
        raise _not_found(evaluation_id) from exc
