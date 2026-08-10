"""One-active-job lifecycle tests for the evaluation harness coordinator."""

from __future__ import annotations

import time

import pytest

from meridian.application.evaluation_coordinator import (
    ActiveEvaluationError,
    EvaluationCoordinator,
    UnknownEvaluationError,
)


def _wait_for_terminal(coordinator: EvaluationCoordinator, evaluation_id: str) -> None:
    deadline = time.monotonic() + 30
    while not coordinator.is_terminal(evaluation_id):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def _write_script(tmp_path, body: str):
    script = tmp_path / "run_eval.py"
    script.write_text(body, encoding="utf-8")
    return script


def test_evaluation_completes_and_captures_output(tmp_path):
    script = _write_script(
        tmp_path,
        "from pathlib import Path\n"
        "print('decision accuracy 10/10')\n"
        "Path('report.md').write_text('# Fresh report', encoding='utf-8')\n",
    )
    coordinator = EvaluationCoordinator(script=script, cwd=tmp_path, report_path=tmp_path / "report.md")

    evaluation = coordinator.start()
    assert coordinator.active_evaluation_id == evaluation.evaluation_id
    _wait_for_terminal(coordinator, evaluation.evaluation_id)

    snapshot = coordinator.get(evaluation.evaluation_id)
    assert snapshot.status == "completed"
    assert snapshot.returncode == 0
    assert "decision accuracy 10/10" in "\n".join(snapshot.output_lines)
    assert snapshot.report is not None
    assert snapshot.report.markdown == "# Fresh report"
    assert coordinator.active_evaluation_id is None


def test_evaluation_failure_is_captured_not_raised(tmp_path):
    script = _write_script(tmp_path, "import sys\nprint('boom')\nsys.exit(1)\n")
    coordinator = EvaluationCoordinator(script=script, cwd=tmp_path)

    evaluation = coordinator.start()
    _wait_for_terminal(coordinator, evaluation.evaluation_id)

    snapshot = coordinator.get(evaluation.evaluation_id)
    assert snapshot.status == "failed"
    assert snapshot.returncode == 1
    assert "boom" in "\n".join(snapshot.output_lines)


def test_evaluation_timeout_terminates_and_reaps_the_process(tmp_path):
    script = _write_script(tmp_path, "import time\nprint('working', flush=True)\ntime.sleep(30)\n")
    coordinator = EvaluationCoordinator(script=script, cwd=tmp_path, timeout_seconds=0.1)

    evaluation = coordinator.start()
    _wait_for_terminal(coordinator, evaluation.evaluation_id)

    snapshot = coordinator.get(evaluation.evaluation_id)
    assert snapshot.status == "failed"
    assert snapshot.terminal is True
    assert snapshot.returncode is not None
    assert snapshot.error_message == "eval harness timed out after 0.1 seconds"
    assert coordinator.active_evaluation_id is None


def test_second_evaluation_is_rejected_while_first_is_active(tmp_path):
    script = _write_script(
        tmp_path,
        "import time\nprint('working')\ntime.sleep(1.5)\n",
    )
    coordinator = EvaluationCoordinator(script=script, cwd=tmp_path)
    first = coordinator.start()

    with pytest.raises(ActiveEvaluationError, match=first.evaluation_id):
        coordinator.start()

    _wait_for_terminal(coordinator, first.evaluation_id)


def test_unknown_evaluation_raises():
    coordinator = EvaluationCoordinator()
    with pytest.raises(UnknownEvaluationError):
        coordinator.get("does-not-exist")
