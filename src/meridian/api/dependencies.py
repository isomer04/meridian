"""Shared API dependencies."""

from __future__ import annotations

from meridian.application.evaluation_coordinator import EvaluationCoordinator
from meridian.application.run_coordinator import RunCoordinator

_coordinator = RunCoordinator()
_evaluation_coordinator = EvaluationCoordinator()


def get_run_coordinator() -> RunCoordinator:
    return _coordinator


def get_evaluation_coordinator() -> EvaluationCoordinator:
    return _evaluation_coordinator
