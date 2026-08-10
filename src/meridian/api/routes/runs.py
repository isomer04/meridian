"""Run lifecycle and Server-Sent Event routes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict
import json
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from meridian.api.dependencies import get_run_coordinator
from meridian.api.models import ErrorEnvelope, ErrorResponse, EventEnvelope, RunResponse, StartRunRequest, StartRunResponse
from meridian.application.models import RunRequest
from meridian.application.run_coordinator import (
    ActiveRunError,
    CoordinatedRun,
    RunCoordinator,
    UnknownRunError,
)

router = APIRouter(tags=["runs"])
DEMO_MODE_ENV = "MERIDIAN_DEMO_MODE"
ENABLED_VALUES = frozenset({"1", "true", "yes"})
AUTHORIZED_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "testclient"})
HEARTBEAT_SECONDS = 1
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}


def _error(status_code: int, code: str, message: str, **context: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ErrorResponse(code=code, message=message, **context).model_dump(),
    )


def _not_found(run_id: str) -> HTTPException:
    return _error(
        status.HTTP_404_NOT_FOUND,
        "run_not_found",
        f"run {run_id!r} was not found",
    )


def _run_response(run: CoordinatedRun) -> RunResponse:
    with run.condition:
        return RunResponse(
            run_id=run.run_id,
            status=run.status,  # type: ignore[arg-type]
            mode=run.mode,
            scenario=run.scenario,
            events=[EventEnvelope(**asdict(event)) for event in run.events],
            trace_lines=run.trace_lines,
            result=run.result,
            approval=run.approval,
            error_type=run.error_type,
            error_message=(
                "The run stopped before a determination could be produced. Review the activity record."
                if run.status == "failed"
                else run.error_message
            ),
            terminal_at=run.terminal_at,
            terminal_persistence_error=run.terminal_persistence_error,
        )


@router.post(
    "/runs",
    response_model=StartRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERROR_RESPONSES,
)
def start_run(
    request: StartRunRequest,
    http_request: Request,
    coordinator: RunCoordinator = Depends(get_run_coordinator),
) -> StartRunResponse:
    demo_mode_enabled = os.environ.get(DEMO_MODE_ENV, "").lower() in ENABLED_VALUES
    client_host = http_request.client.host if http_request.client else None
    auto_approve_authorized = (
        demo_mode_enabled and client_host in AUTHORIZED_LOCAL_HOSTS
    )
    if request.auto_approve and not auto_approve_authorized:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "auto_approve_forbidden",
            "auto-approval requires demonstration mode and an authorized local caller",
        )
    try:
        run = coordinator.start(RunRequest(**request.model_dump()))
    except ActiveRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ErrorResponse(
                code="run_active",
                message="another run is already active",
                active_run_id=exc.run_id,
            ).model_dump(),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(code="invalid_run", message=str(exc)).model_dump(),
        ) from exc
    return StartRunResponse(run_id=run.run_id, status="running")


@router.get("/runs/{run_id}", response_model=RunResponse, responses={404: {"model": ErrorEnvelope}})
def get_run(run_id: str, coordinator: RunCoordinator = Depends(get_run_coordinator)) -> RunResponse:
    try:
        return _run_response(coordinator.snapshot(run_id))
    except UnknownRunError as exc:
        raise _not_found(run_id) from exc


async def _sse_events(
    coordinator: RunCoordinator,
    run_id: str,
    last_event_id: int,
) -> AsyncIterator[str]:
    cursor = last_event_id
    gap_emitted = False
    yield "retry: 1000\n\n"
    while True:
        events = coordinator.events_after(run_id, cursor, timeout=0)
        if not events:
            if coordinator.is_terminal(run_id):
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(HEARTBEAT_SECONDS)
            continue
        if not gap_emitted and events[0].id > cursor + 1:
            gap_emitted = True
            oldest_event = events[0]
            gap = EventEnvelope(
                id=oldest_event.id - 1,
                run_id=run_id,
                type="run.gap",
                actor="RunCoordinator",
                timestamp=oldest_event.timestamp,
                payload={
                    "requested_last_event_id": cursor,
                    "oldest_available_event_id": oldest_event.id,
                },
                line="run.gap retained event history begins later than the requested cursor",
            ).model_dump(mode="json")
            gap_data = json.dumps(gap, separators=(",", ":"))
            yield f"event: run.event\ndata: {gap_data}\n\n"
        for event in events:
            cursor = event.id
            payload = EventEnvelope(**asdict(event)).model_dump(mode="json")
            event_data = json.dumps(payload, separators=(",", ":"))
            yield (
                f"id: {event.id}\n"
                f"event: run.event\n"
                f"data: {event_data}\n\n"
            )
            if event.type == "run.terminal":
                return


@router.get(
    "/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"$ref": "#/components/schemas/EventEnvelope"}}}},
        404: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
async def get_run_events(
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    replay_from: int | None = Query(default=None, alias="last_event_id"),
    coordinator: RunCoordinator = Depends(get_run_coordinator),
) -> StreamingResponse:
    try:
        coordinator.get(run_id)
    except UnknownRunError as exc:
        raise _not_found(run_id) from exc
    try:
        cursor = replay_from if replay_from is not None else int(last_event_id or 0)
    except ValueError as exc:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_last_event_id",
            "Last-Event-ID must be an integer",
        ) from exc
    if cursor < 0:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_last_event_id",
            "Last-Event-ID must not be negative",
        )
    return StreamingResponse(
        _sse_events(coordinator, run_id, cursor),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
