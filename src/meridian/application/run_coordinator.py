"""Process-local coordination for the HTTP run tracer.

The dashboard service remains the sole owner of loan orchestration.  This class only
adapts its lifecycle and the process event bus into replayable, per-run state.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from threading import Condition, RLock, Thread
from typing import Any, Callable
from uuid import uuid4

from meridian.application.dashboard import run_loan, scenario_options
from meridian.application.models import LoanRunResult, RunRequest, RunStatus, RunUpdate, ScenarioOption
from meridian.core.events import BUS, Event
# CrewAI's import graph initializes global hooks. Load it before worker threads so
# `dashboard.run_loan()` only constructs and kicks off the flow on its worker.
from meridian.flow import OriginationFlow as _OriginationFlow  # noqa: F401

TERMINAL_STATUSES = frozenset({"completed", "awaiting_approval", "failed"})
UNSCOPED_EVENT_KINDS = frozenset({"tool.call", "vendor.error", "agent.done", "tool.result"})
TERMINAL_PERSISTENCE_FAILURE_MESSAGE = "The completed run could not be persisted."
log = logging.getLogger(__name__)


class ActiveRunError(RuntimeError):
    """Raised when a request would overlap the process-wide event bus."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"run {run_id} is already active")


class UnknownRunError(KeyError):
    """Raised when a run is absent from bounded coordinator retention."""


@dataclass(frozen=True)
class RunEvent:
    id: int
    run_id: str
    type: str
    actor: str
    loan_id: str | None
    timestamp: str
    payload: dict[str, Any]
    line: str


@dataclass(frozen=True)
class TerminalRun:
    """Immutable terminal data supplied to a completion persistence callback."""

    run_id: str
    status: RunStatus
    result: LoanRunResult | None
    terminal_at: str


@dataclass
class CoordinatedRun:
    run_id: str
    request: RunRequest
    status: RunStatus = "running"
    mode: str | None = None
    scenario: ScenarioOption | None = None
    result: LoanRunResult | None = None
    approval: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    trace_lines: list[str] = field(default_factory=list)
    terminal_at: str | None = None
    terminal_persistence_error: str | None = None
    events: deque[RunEvent] = field(default_factory=deque)
    next_event_id: int = 1
    terminal: bool = False
    unsubscribe: Callable[[], None] | None = None
    on_terminal: Callable[[TerminalRun], None] | None = None
    condition: Condition = field(default_factory=lambda: Condition(RLock()))


class RunCoordinator:
    """Coordinate one active dashboard run and retain bounded replay state."""

    def __init__(self, *, max_runs: int = 20, max_events: int = 500) -> None:
        if max_runs < 1 or max_events < 1:
            raise ValueError("max_runs and max_events must be positive")
        self._max_runs = max_runs
        self._max_events = max_events
        self._runs: OrderedDict[str, CoordinatedRun] = OrderedDict()
        self._active_run_id: str | None = None
        self._lock = RLock()

    @property
    def active_run_id(self) -> str | None:
        with self._lock:
            return self._active_run_id

    def start(
        self,
        request: RunRequest,
        *,
        on_terminal: Callable[[TerminalRun], None] | None = None,
    ) -> CoordinatedRun:
        scenario = next(
            (
                option
                for option in scenario_options()
                if option.scenario_id == request.scenario_id
            ),
            None,
        )
        if scenario is None:
            raise ValueError(f"unknown scenario {request.scenario_id}")

        with self._lock:
            if self._active_run_id is not None:
                raise ActiveRunError(self._active_run_id)

            run = CoordinatedRun(run_id=str(uuid4()), request=request, on_terminal=on_terminal)
            # This subscription intentionally precedes thread startup. `run_loan()` owns
            # the Flow and starts emitting synchronously inside its kickoff call.
            def record_correlated_event(event: Event) -> None:
                if event.loan_id == scenario.loan_id or (
                    event.loan_id is None and event.kind in UNSCOPED_EVENT_KINDS
                ):
                    self._record_event(run, event)

            run.unsubscribe = BUS.subscribe(record_correlated_event)
            self._runs[run.run_id] = run
            self._active_run_id = run.run_id
            self._trim_runs()
            Thread(target=self._execute, args=(run,), daemon=True, name=f"meridian-run-{run.run_id}").start()
            return run

    def get(self, run_id: str) -> CoordinatedRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise UnknownRunError(run_id)
            return run

    def events_after(self, run_id: str, event_id: int, timeout: float = 15.0) -> list[RunEvent]:
        run = self.get(run_id)
        with run.condition:
            available = [event for event in run.events if event.id > event_id]
            if not available and not run.terminal:
                run.condition.wait(timeout)
                available = [event for event in run.events if event.id > event_id]
            return available

    def is_terminal(self, run_id: str) -> bool:
        run = self.get(run_id)
        with run.condition:
            return run.terminal

    def snapshot(self, run_id: str) -> CoordinatedRun:
        """Return the retained run; callers must read its fields under `condition`."""
        return self.get(run_id)

    def _record_event(self, run: CoordinatedRun, event: Event) -> None:
        with run.condition:
            if run.terminal:
                return
            self._append_event(
                run,
                type_=event.kind,
                actor=event.actor,
                loan_id=event.loan_id,
                timestamp=datetime.fromtimestamp(event.ts, timezone.utc).isoformat().replace("+00:00", "Z"),
                payload=dict(event.payload),
                line=event.line(),
            )

    def _execute(self, run: CoordinatedRun) -> None:
        try:
            for update in run_loan(run.request):
                self._apply_update(run, update)
        except Exception as exc:  # defensive boundary around the shared application service
            with run.condition:
                run.status = "failed"
                run.error_type = type(exc).__name__
                run.error_message = str(exc)
        finally:
            if run.unsubscribe is not None:
                try:
                    run.unsubscribe()
                except Exception:  # noqa: BLE001 - terminal bookkeeping must still run
                    log.exception("failed to unsubscribe run %s", run.run_id)
            terminal_callback: Callable[[TerminalRun], None] | None
            terminal_data: TerminalRun
            with run.condition:
                run.terminal_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                self._append_event(
                    run,
                    type_="run.terminal",
                    actor="RunCoordinator",
                    loan_id=None,
                    timestamp=run.terminal_at,
                    payload={"status": run.status, "error_type": run.error_type},
                    line=f"■ run.terminal {run.status}",
                )
                run.terminal = True
                terminal_callback = run.on_terminal
                terminal_data = TerminalRun(
                    run_id=run.run_id,
                    status=run.status,
                    result=run.result,
                    terminal_at=run.terminal_at,
                )
                run.condition.notify_all()
            if terminal_callback is not None:
                for attempt in range(2):
                    try:
                        terminal_callback(terminal_data)
                        break
                    except Exception:  # noqa: BLE001 - persistence boundary
                        if attempt == 1:
                            log.exception("failed to persist terminal run %s", run.run_id)
                            with run.condition:
                                run.terminal_persistence_error = TERMINAL_PERSISTENCE_FAILURE_MESSAGE
                                run.condition.notify_all()
            with self._lock:
                if self._active_run_id == run.run_id:
                    self._active_run_id = None
                self._trim_runs()

    def _apply_update(self, run: CoordinatedRun, update: RunUpdate) -> None:
        with run.condition:
            run.status = update.status
            run.mode = update.mode
            run.scenario = update.scenario
            run.trace_lines = list(update.trace_lines)
            if update.result is not None:
                run.result = update.result
            if update.approval is not None:
                run.approval = update.approval.model_dump(mode="json")
            run.error_type = update.error_type
            run.error_message = update.error_message
            run.condition.notify_all()

    def _append_event(
        self,
        run: CoordinatedRun,
        *,
        type_: str,
        actor: str,
        loan_id: str | None,
        timestamp: str,
        payload: dict[str, Any],
        line: str,
    ) -> None:
        run.events.append(
            RunEvent(
                id=run.next_event_id,
                run_id=run.run_id,
                type=type_,
                actor=actor,
                loan_id=loan_id,
                timestamp=timestamp,
                payload=payload,
                line=line,
            )
        )
        run.next_event_id += 1
        while len(run.events) > self._max_events:
            run.events.popleft()
        run.condition.notify_all()

    def _trim_runs(self) -> None:
        while len(self._runs) > self._max_runs:
            run_id = next(iter(self._runs))
            if run_id == self._active_run_id:
                break
            self._runs.popitem(last=False)
