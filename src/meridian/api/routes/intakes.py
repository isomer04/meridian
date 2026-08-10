"""Deterministic PDF intake and decision-package routes."""

from __future__ import annotations

from copy import deepcopy
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from meridian.api.dependencies import get_run_coordinator
from meridian.api.models import ErrorEnvelope, IntakeConfirmationBody, IntakeRunRequest, StartRunResponse
from meridian.application.document_intake import (
    MAX_FILE_BYTES, MAX_PACKAGE_BYTES, IntakeConflictError, IntakeError, confirm_draft,
    create_draft, decision_package_pdf, get_draft, public_draft,
)
from meridian.application.models import RunRequest
from meridian.application.run_coordinator import ActiveRunError, RunCoordinator
from meridian.core.db import get_db
from meridian.vendors.fixtures import by_number, register

router = APIRouter(tags=["intakes"])
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_INTAKE_WORKERS = anyio.CapacityLimiter(2)
_INTAKE_ERRORS = {422: {"model": ErrorEnvelope, "description": "Invalid document intake"}}
_NOT_FOUND = {404: {"model": ErrorEnvelope, "description": "Intake or run not found"}}
_CONFLICT = {409: {"model": ErrorEnvelope, "description": "Intake or run state conflict"}}


def _error(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": "intake_error", "message": message})


@router.post("/intakes", status_code=status.HTTP_201_CREATED, responses=_INTAKE_ERRORS)
async def upload_intake(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    try:
        payloads: list[tuple[str, bytes]] = []
        package_bytes = 0
        for upload in files:
            chunks: list[bytes] = []
            file_bytes = 0
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                file_bytes += len(chunk)
                package_bytes += len(chunk)
                if file_bytes > MAX_FILE_BYTES:
                    raise IntakeError(f"{upload.filename or 'document.pdf'} exceeds the 15 MB file limit.")
                if package_bytes > MAX_PACKAGE_BYTES:
                    raise IntakeError("Document package exceeds the 40 MB limit.")
                chunks.append(chunk)
            payloads.append((upload.filename or "document.pdf", b"".join(chunks)))
        draft = await anyio.to_thread.run_sync(
            partial(create_draft, payloads),
            limiter=_INTAKE_WORKERS,
        )
        return public_draft(draft)
    except IntakeError as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/intakes/{intake_id}", responses=_NOT_FOUND | _INTAKE_ERRORS)
def read_intake(intake_id: str) -> dict[str, Any]:
    try:
        return public_draft(get_draft(intake_id))
    except KeyError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "Intake draft was not found.") from exc
    except IntakeError as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/intakes/{intake_id}/confirm", responses=_NOT_FOUND | _CONFLICT | _INTAKE_ERRORS)
def confirm_intake(intake_id: str, body: IntakeConfirmationBody) -> dict[str, Any]:
    try:
        return public_draft(confirm_draft(intake_id, body.case, body.reviewer, body.expected_revision))
    except KeyError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "Intake draft was not found.") from exc
    except IntakeConflictError as exc:
        raise _error(status.HTTP_409_CONFLICT, str(exc)) from exc
    except IntakeError as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _scenario_for_draft(draft: dict[str, Any]) -> dict[str, Any]:
    case = draft.get("confirmed_case")
    if not case:
        raise IntakeError("Confirm the extracted case before starting a run.")
    base = deepcopy(by_number(1))
    if not base:
        raise IntakeError("The local simulated-vendor baseline is unavailable.")
    suffix = draft["intake_id"].replace("-", "")[:10].upper()
    scenario_id = 1_000_000 + int(draft["intake_id"].replace("-", "")[:6], 16)
    base.update({
        "scenario_id": scenario_id,
        "loan_id": f"MER-PDF-{suffix}",
        "name": "Confirmed PDF intake (simulated vendors)",
        "lands": "Borrower, property, and loan facts were confirmed from uploaded documents; vendor responses remain local simulations.",
        "borrower": case["borrower"],
        "property": case["property"],
        "loan": case["loan"],
        "le_delivered": bool(case.get("le_delivered", False)),
        "intent_to_proceed": bool(case.get("intent_to_proceed", False)),
        "documents": [{
            "filename": item["filename"], "sha256": item["sha256"],
            "page_count": item["page_count"], "source": "confirmed_pdf_intake",
        } for item in draft["documents"]],
    })
    base.pop("expected", None)
    register(base)
    return base


@router.post(
    "/intakes/{intake_id}/runs", response_model=StartRunResponse,
    status_code=status.HTTP_202_ACCEPTED, responses=_NOT_FOUND | _CONFLICT | _INTAKE_ERRORS,
)
def start_intake_run(
    intake_id: str,
    body: IntakeRunRequest,
    coordinator: RunCoordinator = Depends(get_run_coordinator),
) -> StartRunResponse:
    try:
        draft = get_draft(intake_id)
        scenario = _scenario_for_draft(draft)
        source_document_digests = [document["sha256"] for document in draft["documents"]]

        def persist_completed_run(completed_run: Any) -> None:
            if (
                completed_run.status == "completed"
                and completed_run.result is not None
                and completed_run.terminal_at is not None
            ):
                get_db().save_intake_run_record(
                    completed_run.run_id,
                    intake_id,
                    completed_run.result.model_dump(mode="json"),
                    source_document_digests,
                    completed_run.terminal_at,
                )

        run = coordinator.start(
            RunRequest(scenario_id=scenario["scenario_id"], **body.model_dump()),
            on_terminal=persist_completed_run,
        )
        return StartRunResponse(run_id=run.run_id, status="running")
    except KeyError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "Intake draft was not found.") from exc
    except ActiveRunError as exc:
        raise HTTPException(status_code=409, detail={"code": "run_active", "message": "another run is already active", "active_run_id": exc.run_id}) from exc
    except (IntakeError, ValueError) as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get(
    "/runs/{run_id}/decision-package.pdf",
    response_class=Response,
    responses={
        200: {
            "description": "Decision package PDF attachment",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
            "headers": {"Content-Disposition": {
                "description": "Attachment filename", "schema": {"type": "string"},
            }},
        },
        **_NOT_FOUND,
        **_CONFLICT,
    },
)
def download_decision_package(
    run_id: str,
    coordinator: RunCoordinator = Depends(get_run_coordinator),
) -> Response:
    record = get_db().load_intake_run_record(run_id)
    if record is None:
        try:
            run = coordinator.snapshot(run_id)
        except KeyError as exc:
            raise _error(status.HTTP_404_NOT_FOUND, "Run was not found.") from exc
        if run.terminal_persistence_error is not None:
            raise _error(
                status.HTTP_409_CONFLICT,
                "The completed run could not be persisted; its decision package is unavailable.",
            )
        raise _error(status.HTTP_409_CONFLICT, "A decision package is available only after a completed run.")
    package_data = record["result"] | {"terminal_at": record["terminal_at"]}
    source_document_digests = record["source_document_digests"]
    if source_document_digests is None:
        try:
            legacy_draft = get_draft(record["intake_id"])
        except (KeyError, IntakeError) as exc:
            raise _error(
                status.HTTP_409_CONFLICT,
                "The completed run's source document provenance is unavailable.",
            ) from exc
        source_document_digests = [document["sha256"] for document in legacy_draft["documents"]]
    content = decision_package_pdf(package_data, source_document_digests)
    filename = f"{record['result']['loan_id']}-decision-package.pdf"
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{Path(filename).name}"'})
