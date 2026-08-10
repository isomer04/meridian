"""Process-local coordination for the HTTP evaluation harness runner.

Mirrors `run_coordinator.RunCoordinator`: one active job at a time, bounded
retained output, and a terminal snapshot the API can poll. The harness
subprocess itself remains the sole owner of evaluation behavior; this class
only adapts its lifecycle into pollable state.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Condition, RLock, Thread
import logging
import subprocess
import sys
from typing import Literal, TextIO
from uuid import uuid4

from meridian.application.evaluation_service import (
    DEFAULT_EVAL_SCRIPT,
    PROJECT_ROOT,
    EvaluationReport,
    load_evaluation_report,
)

EvaluationStatus = Literal["running", "completed", "failed"]
log = logging.getLogger(__name__)


class ActiveEvaluationError(RuntimeError):
    """Raised when a request would overlap the one active evaluation job."""

    def __init__(self, evaluation_id: str):
        self.evaluation_id = evaluation_id
        super().__init__(f"evaluation {evaluation_id} is already active")


class UnknownEvaluationError(KeyError):
    """Raised when an evaluation id is absent from bounded coordinator retention."""


@dataclass
class CoordinatedEvaluation:
    evaluation_id: str
    status: EvaluationStatus = "running"
    returncode: int | None = None
    output_lines: deque[str] = field(default_factory=deque)
    report: EvaluationReport | None = None
    error_message: str | None = None
    started_at: str = ""
    terminal_at: str | None = None
    terminal: bool = False
    condition: Condition = field(default_factory=lambda: Condition(RLock()))


class EvaluationCoordinator:
    """Coordinate one active eval-harness subprocess and retain bounded logs."""

    def __init__(
        self,
        *,
        max_evaluations: int = 10,
        max_lines: int = 2000,
        script: Path | None = None,
        cwd: Path | None = None,
        report_path: Path | None = None,
        timeout_seconds: float = 300,
    ) -> None:
        if max_evaluations < 1 or max_lines < 1 or timeout_seconds <= 0:
            raise ValueError("max_evaluations, max_lines, and timeout_seconds must be positive")
        self._max_evaluations = max_evaluations
        self._max_lines = max_lines
        self._script = script or DEFAULT_EVAL_SCRIPT
        self._cwd = cwd or PROJECT_ROOT
        self._report_path = report_path
        self._timeout_seconds = timeout_seconds
        self._evaluations: OrderedDict[str, CoordinatedEvaluation] = OrderedDict()
        self._active_id: str | None = None
        self._lock = RLock()

    @property
    def active_evaluation_id(self) -> str | None:
        with self._lock:
            return self._active_id

    @property
    def report_path(self) -> Path | None:
        return self._report_path

    def start(self) -> CoordinatedEvaluation:
        with self._lock:
            if self._active_id is not None:
                raise ActiveEvaluationError(self._active_id)
            evaluation = CoordinatedEvaluation(
                evaluation_id=str(uuid4()),
                started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            self._evaluations[evaluation.evaluation_id] = evaluation
            self._active_id = evaluation.evaluation_id
            self._trim()
            Thread(
                target=self._execute,
                args=(evaluation,),
                daemon=True,
                name=f"meridian-eval-{evaluation.evaluation_id}",
            ).start()
            return evaluation

    def get(self, evaluation_id: str) -> CoordinatedEvaluation:
        with self._lock:
            evaluation = self._evaluations.get(evaluation_id)
            if evaluation is None:
                raise UnknownEvaluationError(evaluation_id)
            return evaluation

    def is_terminal(self, evaluation_id: str) -> bool:
        evaluation = self.get(evaluation_id)
        with evaluation.condition:
            return evaluation.terminal

    def _execute(self, evaluation: CoordinatedEvaluation) -> None:
        process: subprocess.Popen[str] | None = None
        try:
            with subprocess.Popen(  # noqa: S603 - fixed interpreter and configured eval script
                [sys.executable, str(self._script)],
                cwd=str(self._cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            ) as process:
                try:
                    assert process.stdout is not None
                    reader = Thread(
                        target=self._consume_output,
                        args=(evaluation, process.stdout),
                        daemon=True,
                        name=f"meridian-eval-output-{evaluation.evaluation_id}",
                    )
                    reader.start()
                    try:
                        returncode = process.wait(timeout=self._timeout_seconds)
                    except subprocess.TimeoutExpired:
                        self._terminate(process)
                        reader.join(timeout=5)
                        with evaluation.condition:
                            evaluation.returncode = process.returncode
                            evaluation.status = "failed"
                            evaluation.error_message = (
                                f"eval harness timed out after {self._timeout_seconds:g} seconds"
                            )
                    else:
                        reader.join()
                        with evaluation.condition:
                            evaluation.returncode = returncode
                            evaluation.status = "completed" if returncode == 0 else "failed"
                            if returncode != 0:
                                evaluation.error_message = (
                                    f"eval harness exited with status {returncode}"
                                )
                except Exception:  # noqa: BLE001 - terminate before leaving Popen context
                    self._terminate(process)
                    raise
        except Exception as exc:  # noqa: BLE001 - defensive subprocess boundary
            if process is not None:
                self._terminate(process)
            with evaluation.condition:
                evaluation.status = "failed"
                evaluation.error_message = str(exc)
        finally:
            report = None
            try:
                report = load_evaluation_report(self._report_path)
            except (OSError, UnicodeError):
                log.exception("failed to load evaluation report for %s", evaluation.evaluation_id)
            with evaluation.condition:
                evaluation.report = report
                evaluation.terminal = True
                evaluation.terminal_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                evaluation.condition.notify_all()
            with self._lock:
                if self._active_id == evaluation.evaluation_id:
                    self._active_id = None
                self._trim()

    def _consume_output(self, evaluation: CoordinatedEvaluation, stdout: TextIO) -> None:
        try:
            for line in stdout:
                self._append_line(evaluation, line.rstrip("\n"))
        except ValueError:
            if not stdout.closed:
                raise

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _append_line(self, evaluation: CoordinatedEvaluation, line: str) -> None:
        with evaluation.condition:
            evaluation.output_lines.append(line)
            while len(evaluation.output_lines) > self._max_lines:
                evaluation.output_lines.popleft()
            evaluation.condition.notify_all()

    def _trim(self) -> None:
        while len(self._evaluations) > self._max_evaluations:
            evaluation_id = next(iter(self._evaluations))
            if evaluation_id == self._active_id:
                break
            self._evaluations.popitem(last=False)
