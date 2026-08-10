"""Run coordinator lifecycle, race, retention, and cleanup tests."""

from __future__ import annotations

import time
from threading import Event as ThreadEvent

import pytest

from meridian.application.models import RunRequest, RunUpdate, ScenarioOption
from meridian.application.run_coordinator import ActiveRunError, RunCoordinator
from meridian.core.events import BUS


@pytest.fixture(autouse=True)
def clear_bus():
    BUS.clear()
    yield
    BUS.clear()


def _update(status: str = "running") -> RunUpdate:
    return RunUpdate(
        status=status,  # type: ignore[arg-type]
        scenario=ScenarioOption(scenario_id=1, label="1. Scenario", name="Scenario", loan_id="MER-1001"),
        mode="replay",
    )


def _wait_for_terminal(coordinator: RunCoordinator, run_id: str) -> None:
    deadline = time.monotonic() + 2
    while not coordinator.is_terminal(run_id):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_subscribes_before_kickoff_and_replays_first_event(monkeypatch):
    def fake_run(_request):
        BUS.emit("flow.step", actor="Flow", loan_id="MER-1001", step="first")
        yield _update()
        yield _update("completed")

    monkeypatch.setattr("meridian.application.run_coordinator.run_loan", fake_run)
    coordinator = RunCoordinator()

    run = coordinator.start(RunRequest(scenario_id=1))
    _wait_for_terminal(coordinator, run.run_id)

    events = coordinator.events_after(run.run_id, 0, timeout=0)
    assert [event.type for event in events] == ["flow.step", "run.terminal"]
    assert [event.id for event in events] == [1, 2]


def test_second_run_is_rejected_while_first_is_active(monkeypatch):
    release = ThreadEvent()

    def fake_run(_request):
        yield _update()
        release.wait(2)
        yield _update("completed")

    monkeypatch.setattr("meridian.application.run_coordinator.run_loan", fake_run)
    coordinator = RunCoordinator()
    first = coordinator.start(RunRequest(scenario_id=1))

    with pytest.raises(ActiveRunError, match=first.run_id):
        coordinator.start(RunRequest(scenario_id=1))
    release.set()
    _wait_for_terminal(coordinator, first.run_id)


def test_subscriptions_are_cleaned_after_success_and_exception(monkeypatch):
    baseline = len(BUS._subscribers)

    def success(_request):
        yield _update()
        yield _update("completed")

    monkeypatch.setattr("meridian.application.run_coordinator.run_loan", success)
    coordinator = RunCoordinator()
    run = coordinator.start(RunRequest(scenario_id=1))
    _wait_for_terminal(coordinator, run.run_id)
    assert len(BUS._subscribers) == baseline

    def broken(_request):
        yield _update()
        raise RuntimeError("boom")

    monkeypatch.setattr("meridian.application.run_coordinator.run_loan", broken)
    failed = coordinator.start(RunRequest(scenario_id=1))
    _wait_for_terminal(coordinator, failed.run_id)
    assert coordinator.snapshot(failed.run_id).status == "failed"
    assert len(BUS._subscribers) == baseline


def test_terminal_persistence_retries_then_exposes_a_terminal_failure(monkeypatch):
    def success(_request):
        yield _update("completed")

    monkeypatch.setattr("meridian.application.run_coordinator.run_loan", success)
    attempts = 0

    def persist(_run):
        nonlocal attempts
        attempts += 1
        raise OSError("database unavailable")

    coordinator = RunCoordinator()
    run = coordinator.start(RunRequest(scenario_id=1), on_terminal=persist)
    _wait_for_terminal(coordinator, run.run_id)

    assert attempts == 2
    deadline = time.monotonic() + 2
    while coordinator.snapshot(run.run_id).terminal_persistence_error is None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert coordinator.snapshot(run.run_id).terminal_persistence_error == (
        "The completed run could not be persisted."
    )


def test_terminal_callback_runs_after_terminal_state_releases_the_run_lock(monkeypatch):
    release = ThreadEvent()

    def success(_request):
        yield _update()
        release.wait(2)
        yield _update("completed")

    monkeypatch.setattr("meridian.application.run_coordinator.run_loan", success)
    coordinator = RunCoordinator()
    run = coordinator.start(RunRequest(scenario_id=1))
    lock_available = ThreadEvent()

    def persist(terminal):
        assert terminal.run_id == run.run_id
        assert coordinator.is_terminal(run.run_id)

        def acquire_run_lock():
            with run.condition:
                lock_available.set()

        worker = Thread(target=acquire_run_lock)
        worker.start()
        assert lock_available.wait(1)
        worker.join()

    run.on_terminal = persist
    release.set()
    _wait_for_terminal(coordinator, run.run_id)


def test_replay_cursor_does_not_duplicate_events(monkeypatch):
    def fake_run(_request):
        BUS.emit("flow.step", actor="Flow", loan_id="MER-1001", step="one")
        BUS.emit("flow.step", actor="Flow", loan_id="MER-1001", step="two")
        yield _update()
        yield _update("completed")

    monkeypatch.setattr("meridian.application.run_coordinator.run_loan", fake_run)
    coordinator = RunCoordinator()
    run = coordinator.start(RunRequest(scenario_id=1))
    _wait_for_terminal(coordinator, run.run_id)

    first_connection = coordinator.events_after(run.run_id, 0, timeout=0)
    second_connection = coordinator.events_after(run.run_id, first_connection[1].id, timeout=0)
    assert [event.id for event in first_connection] == [1, 2, 3]
    assert [event.id for event in second_connection] == [3]
