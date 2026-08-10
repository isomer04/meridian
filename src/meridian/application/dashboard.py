"""Dashboard use cases without a dependency on any UI framework or HTTP transport."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

from meridian.core import db as db_mod
from meridian.core.errors import ApprovalRequired
from meridian.core.events import BUS, Event
from meridian.core.replay import resolve_mode
from meridian.vendors import credit_bureau
from meridian.vendors.fixtures import all_scenarios, by_number

from .models import (
    ApprovalDecisionRequest,
    ApprovalItem,
    IdempotencyView,
    RunRequest,
    RunUpdate,
    ScenarioOption,
)
from .presenters import present_approval, present_event, present_loan_state

_RUN_LOCK = Lock()
RUN_LOCK_TIMEOUT_SECONDS = 1


def scenario_options() -> list[ScenarioOption]:
    return [
        ScenarioOption(
            scenario_id=int(scenario["scenario_id"]),
            label=f"{scenario['scenario_id']}. {scenario['name']}",
            name=str(scenario["name"]),
            loan_id=str(scenario["loan_id"]),
            expected_decision=(scenario.get("expected") or {}).get("decision"),
            summary=scenario.get("lands"),
        )
        for scenario in all_scenarios()
    ]


def scenario_label_map() -> dict[str, int]:
    return {option.label: option.scenario_id for option in scenario_options()}


def approval_queue() -> list[ApprovalItem]:
    return [present_approval(row) for row in db_mod.get_db().approval_queue()]


def approval(loan_id: str, gate: str) -> ApprovalItem | None:
    row = db_mod.get_db().approval(loan_id.strip(), gate.strip().upper())
    return present_approval(row) if row is not None else None


def decide_approval(request: ApprovalDecisionRequest) -> None:
    db_mod.get_db().record_approval(
        request.loan_id.strip(),
        request.gate.strip().upper(),
        request.artifact_digest.strip(),
        request.decision,
        request.approver,
    )


def _judgment(kind: str, mode: str, roster: str):
    if kind == "crew":
        from meridian.crews import CrewJudgment

        return CrewJudgment(mode=mode, roster=roster)
    from meridian.judgment import StubJudgment

    return StubJudgment()


def _trace_header(scenario: dict[str, Any], request: RunRequest, mode: str) -> list[str]:
    return [
        f"scenario {request.scenario_id} — {scenario['name']}",
        f"{scenario['loan_id']}   judgment={request.judgment_kind}   llm={mode}",
        "─" * 78,
        "",
    ]


def _events(records: list[Event]):
    return [present_event(event) for event in records]


@contextmanager
def _collect_events() -> Iterator[list[Event]]:
    collected: list[Event] = []
    BUS.clear()
    unsubscribe = BUS.subscribe(collected.append)
    try:
        yield collected
    finally:
        unsubscribe()


def run_loan(request: RunRequest) -> Iterator[RunUpdate]:
    """Run one dashboard scenario and yield framework-neutral lifecycle updates."""
    acquired = _RUN_LOCK.acquire(timeout=RUN_LOCK_TIMEOUT_SECONDS)
    if not acquired:
        raise TimeoutError("another loan run is already active")
    try:
        yield from _run_loan(request)
    finally:
        _RUN_LOCK.release()


def _run_loan(request: RunRequest) -> Iterator[RunUpdate]:
    from meridian.flow import OriginationFlow

    scenario = by_number(request.scenario_id)
    if scenario is None:
        raise ValueError(f"unknown scenario {request.scenario_id}")
    option = ScenarioOption(
        scenario_id=request.scenario_id,
        label=f"{request.scenario_id}. {scenario['name']}",
        name=str(scenario["name"]),
        loan_id=str(scenario["loan_id"]),
        expected_decision=(scenario.get("expected") or {}).get("decision"),
        summary=scenario.get("lands"),
    )
    mode = resolve_mode("auto")
    db = db_mod.get_db()
    if db.load_loan_state(option.loan_id) is None:
        with db.transaction():
            db.save_loan_state(option.loan_id, "new", {}, scenario=option.name)

    trace_lines = _trace_header(scenario, request, mode)
    with _collect_events() as collected:
        yield RunUpdate(
            status="running",
            scenario=option,
            mode=mode,
            trace_lines=list(trace_lines),
        )
        try:
            flow = OriginationFlow(
                loan_id=option.loan_id,
                judgment=_judgment(request.judgment_kind, mode, request.roster),
                mode=mode,
                inject_policy_attack=request.policy_attack,
                auto_approve=request.auto_approve,
            )
            flow.kickoff()
            state = flow.state
        except ApprovalRequired as exc:
            trace_lines += [event.line() for event in collected]
            trace_lines += ["", f"HUMAN APPROVAL REQUIRED: {exc}"]
            pending = next(
                (
                    item
                    for item in approval_queue()
                    if item.loan_id == exc.loan_id and item.gate == exc.gate
                ),
                None,
            )
            yield RunUpdate(
                status="awaiting_approval",
                scenario=option,
                mode=mode,
                events=_events(collected),
                trace_lines=trace_lines,
                approval=pending,
            )
            return
        except Exception as exc:  # noqa: BLE001 - terminal UI state records the failure
            trace_lines += [event.line() for event in collected]
            trace_lines += ["", f"RUN FAILED: {type(exc).__name__}: {exc}"]
            yield RunUpdate(
                status="failed",
                scenario=option,
                mode=mode,
                events=_events(collected),
                trace_lines=trace_lines,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return

    trace_lines += [event.line() for event in collected]
    all_events = list(collected)
    idempotency = IdempotencyView(enabled=request.submit_twice)

    if request.submit_twice:
        before = credit_bureau.inquiry_count(option.loan_id)
        with _collect_events() as second_events:
            second_flow = OriginationFlow(
                loan_id=option.loan_id,
                judgment=_judgment(request.judgment_kind, mode, request.roster),
                mode=mode,
                auto_approve=request.auto_approve,
            )
            second_flow.kickoff()
        after = credit_bureau.inquiry_count(option.loan_id)
        trace_lines += ["", "─" * 78, "SECOND SUBMISSION — same loan, same file", "─" * 78, ""]
        trace_lines += [event.line() for event in second_events]
        all_events += second_events
        idempotency = IdempotencyView(
            enabled=True,
            calls_before_second_run=before,
            calls_after_second_run=after,
        )

    yield RunUpdate(
        status="completed",
        scenario=option,
        mode=mode,
        events=_events(all_events),
        trace_lines=trace_lines,
        result=present_loan_state(state, db, idempotency=idempotency),
    )
