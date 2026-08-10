"""Phase 2 FastAPI tracer contract tests."""

from __future__ import annotations

import json
import time
from threading import Event as ThreadEvent

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_evaluation_coordinator, get_run_coordinator
from meridian.api.routes import documents as document_routes
from meridian.api.routes.system import _crew_setup_action
from meridian.application.evaluation_coordinator import EvaluationCoordinator
from meridian.application.models import RunStatus, RunUpdate, ScenarioOption
from meridian.application.run_coordinator import RunCoordinator
from meridian.core import db as db_mod
from meridian.core.events import BUS
from meridian.models import Decision


@pytest.fixture()
def client():
    app = create_app()
    coordinator = RunCoordinator()
    app.dependency_overrides[get_run_coordinator] = lambda: coordinator
    with TestClient(app) as test_client:
        yield test_client, coordinator
    BUS.clear()


@pytest.fixture()
def eval_client(tmp_path):
    app = create_app()
    coordinator = EvaluationCoordinator(
        script=tmp_path / "run_eval.py",
        cwd=tmp_path,
        report_path=tmp_path / "report.md",
    )
    app.dependency_overrides[get_evaluation_coordinator] = lambda: coordinator
    with TestClient(app) as test_client:
        yield test_client, coordinator, tmp_path


def _wait_for_evaluation_terminal(coordinator: EvaluationCoordinator, evaluation_id: str) -> None:
    deadline = time.monotonic() + 30
    while not coordinator.is_terminal(evaluation_id):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def _update(status: RunStatus = "running") -> RunUpdate:
    return RunUpdate(
        status=status,  # type: ignore[arg-type]
        scenario=ScenarioOption(scenario_id=1, label="1. Scenario", name="Scenario", loan_id="MER-1001"),
        mode="replay",
    )


def _wait_for_terminal(coordinator: RunCoordinator, run_id: str) -> None:
    deadline = time.monotonic() + 30
    while not coordinator.is_terminal(run_id):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def _sse_frames(body: str) -> list[tuple[int | None, dict[str, object]]]:
    frames = []
    for block in body.split("\n\n"):
        if "data: " not in block:
            continue
        lines = block.splitlines()
        event_id = next((int(line[4:]) for line in lines if line.startswith("id: ")), None)
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        frames.append((event_id, data))
    return frames


def _sse_payloads(body: str) -> list[dict[str, object]]:
    return [payload for _, payload in _sse_frames(body)]


def test_system_scenarios_and_error_response_models(client):
    test_client, _ = client

    system = test_client.get("/api/v1/system")
    assert system.status_code == 200
    assert system.json()["default_judgment"] in {"stub", "crew"}

    scenarios = test_client.get("/api/v1/scenarios")
    assert scenarios.status_code == 200
    first_scenario = scenarios.json()[0]
    assert first_scenario["loan_id"] == "MER-1001"
    assert first_scenario["summary"]

    missing = test_client.get("/api/v1/runs/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "run_not_found"

    invalid = test_client.post("/api/v1/runs", json={"scenario_id": 999})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == {
        "code": "invalid_run",
        "message": "unknown scenario 999",
        "active_run_id": None,
        "active_evaluation_id": None,
    }


def test_active_conflict_and_sse_reconnect_replays_without_duplicates(client, monkeypatch):
    test_client, coordinator = client
    release = ThreadEvent()

    def fake_run(_request):
        BUS.emit("flow.step", actor="Flow", loan_id="MER-1001", step="one")
        yield _update()
        release.wait(2)
        BUS.emit("flow.step", actor="Flow", loan_id="MER-1001", step="two")
        yield _update("completed")

    monkeypatch.setattr("meridian.application.run_coordinator.run_loan", fake_run)
    started = test_client.post("/api/v1/runs", json={"scenario_id": 1})
    run_id = started.json()["run_id"]
    conflict = test_client.post("/api/v1/runs", json={"scenario_id": 1})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["active_run_id"] == run_id

    release.set()
    _wait_for_terminal(coordinator, run_id)
    first_frames = _sse_frames(test_client.get(f"/api/v1/runs/{run_id}/events").text)
    first = [payload for _, payload in first_frames]
    replay_frames = _sse_frames(
        test_client.get(f"/api/v1/runs/{run_id}/events?last_event_id={first_frames[0][0]}").text
    )
    replay = [payload for _, payload in replay_frames]
    negative_cursor = test_client.get(f"/api/v1/runs/{run_id}/events?last_event_id=-1")
    assert negative_cursor.status_code == 422
    assert negative_cursor.json()["detail"]["code"] == "invalid_last_event_id"
    assert [event["id"] for event in first] == [1, 2, 3]
    assert [event["id"] for event in replay] == [2, 3]
    assert [event_id for event_id, _ in first_frames] == [1, 2, 3]
    assert [event_id for event_id, _ in replay_frames] == [2, 3]
    assert replay[-1]["type"] == "run.terminal"
    assert "event: run.event" in test_client.get(f"/api/v1/runs/{run_id}/events").text


def test_scenario_one_stub_run_completes_against_scratch_database(client, tmp_path):
    test_client, coordinator = client
    database = db_mod.Database(tmp_path / "api-scenario.db")
    db_mod.set_db(database)
    BUS.clear()
    try:
        response = test_client.post("/api/v1/runs", json={"scenario_id": 1, "judgment_kind": "stub"})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        _wait_for_terminal(coordinator, run_id)

        run = test_client.get(f"/api/v1/runs/{run_id}")
        assert run.status_code == 200
        snapshot = run.json()
        assert snapshot["status"] == "completed"
        assert snapshot["scenario"]["loan_id"] == "MER-1001"
        assert snapshot["trace_lines"]
        assert snapshot["events"]
        assert snapshot["terminal_at"]
        assert snapshot["result"]["decision"] == Decision.APPROVED.value

        events = _sse_payloads(test_client.get(f"/api/v1/runs/{run_id}/events").text)
        assert events[0]["type"] == "flow.step"
        assert events[-1]["type"] == "run.terminal"
    finally:
        database.close()
        db_mod.set_db(None)
        BUS.clear()


def test_run_request_rejects_unknown_fields_and_unauthorized_auto_approval(client):
    test_client, _ = client

    unknown_field = test_client.post(
        "/api/v1/runs", json={"scenario_id": 1, "scenerio_id": 1}
    )
    assert unknown_field.status_code == 422

    auto_approve = test_client.post(
        "/api/v1/runs", json={"scenario_id": 1, "auto_approve": True}
    )
    assert auto_approve.status_code == 403
    assert auto_approve.json()["detail"]["code"] == "auto_approve_forbidden"


@pytest.fixture()
def api_db(tmp_path):
    database = db_mod.Database(tmp_path / "api-approvals.db")
    db_mod.set_db(database)
    BUS.clear()
    yield database
    database.close()
    db_mod.set_db(None)
    BUS.clear()


def test_approval_queue_and_decision_round_trip(client, api_db):
    test_client, _ = client

    empty = test_client.get("/api/v1/approvals")
    assert empty.status_code == 200
    assert empty.json() == {"approvals": []}

    api_db.request_approval("MER-9001", "G2", {"kind": "overlay_exception", "version": 1})
    queued = test_client.get("/api/v1/approvals")
    assert queued.status_code == 200
    [item] = queued.json()["approvals"]
    assert item["loan_id"] == "MER-9001"
    assert item["gate"] == "G2"
    assert item["status"] == "pending"
    digest = item["artifact_digest"]

    missing = test_client.post(
        "/api/v1/approvals/MER-DOES-NOT-EXIST/G2/decision",
        json={"artifact_digest": digest, "decision": "approved", "approver": "Avery Reviewer"},
    )
    assert missing.status_code == 404
    assert missing.json() == {
        "code": "approval_not_found",
        "message": "approval not found",
        "active_run_id": None,
        "active_evaluation_id": None,
    }


    malformed = test_client.post(
        "/api/v1/approvals/MER-9001/G2/decision",
        content="{",
        headers={"content-type": "application/json"},
    )
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "request_validation_error"

    invalid_decision = test_client.post(
        "/api/v1/approvals/MER-9001/G2/decision",
        json={"artifact_digest": digest, "decision": "maybe", "approver": "Avery Reviewer"},
    )
    assert invalid_decision.status_code == 422
    assert invalid_decision.json()["code"] == "invalid_decision"

    blank_approver = test_client.post(
        "/api/v1/approvals/MER-9001/G2/decision",
        json={"artifact_digest": digest, "decision": "approved", "approver": "   "},
    )
    assert blank_approver.status_code == 422
    assert blank_approver.json()["code"] == "approver_required"

    stale = test_client.post(
        "/api/v1/approvals/MER-9001/G2/decision",
        json={"artifact_digest": "not-the-real-digest", "decision": "approved", "approver": "Avery Reviewer"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_approval"

    decided = test_client.post(
        "/api/v1/approvals/MER-9001/G2/decision",
        json={"artifact_digest": digest, "decision": "approved", "approver": "Avery Reviewer"},
    )
    assert decided.status_code == 204

    after = test_client.get("/api/v1/approvals")
    assert after.json() == {"approvals": []}
    recorded = api_db.approval("MER-9001", "G2")
    assert recorded is not None
    assert recorded["status"] == "approved"
    assert recorded["approver"] == "Avery Reviewer"

    replay = test_client.post(
        "/api/v1/approvals/MER-9001/G2/decision",
        json={"artifact_digest": digest, "decision": "approved", "approver": "Avery Reviewer"},
    )
    assert replay.status_code == 409
    assert replay.json()["code"] == "stale_approval"


@pytest.mark.parametrize(
    ("model", "expected_variable"),
    [
        ("deepseek/deepseek-v4-flash", "DEEPSEEK_API_KEY"),
        ("gpt-4o-mini", "OPENAI_API_KEY"),
    ],
)
def test_crew_setup_action_targets_configured_provider(monkeypatch, model, expected_variable):
    for variable in (
        "MERIDIAN_MODEL",
        "MODEL_FAST",
        "MODEL_STRONG",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("MODEL", model)

    assert _crew_setup_action() == f"Set {expected_variable} and restart the API."


def test_evaluation_report_missing_and_present_states(eval_client):
    test_client, _, tmp_path = eval_client

    missing = test_client.get("/api/v1/evaluation/report")
    assert missing.status_code == 200
    assert missing.json()["exists"] is False
    assert "No evaluation report" in missing.json()["markdown"]

    (tmp_path / "report.md").write_text("# Fresh report", encoding="utf-8")
    present = test_client.get("/api/v1/evaluation/report")
    assert present.status_code == 200
    assert present.json()["exists"] is True
    assert present.json()["markdown"] == "# Fresh report"


def test_evaluation_run_completes_and_duplicate_job_gets_409(eval_client):
    test_client, coordinator, tmp_path = eval_client
    (tmp_path / "run_eval.py").write_text(
        "import time\n"
        "from pathlib import Path\n"
        "print('decision accuracy 10/10')\n"
        "Path('report.md').write_text('# 10/10', encoding='utf-8')\n"
        "while not Path('release-eval').exists():\n"
        "    time.sleep(0.01)\n",
        encoding="utf-8",
    )

    started = test_client.post("/api/v1/evaluations")
    assert started.status_code == 202
    evaluation_id = started.json()["evaluation_id"]

    try:
        conflict = test_client.post("/api/v1/evaluations")
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "evaluation_active"
        assert conflict.json()["detail"]["active_evaluation_id"] == evaluation_id
    finally:
        (tmp_path / "release-eval").touch()

    _wait_for_evaluation_terminal(coordinator, evaluation_id)

    snapshot = test_client.get(f"/api/v1/evaluations/{evaluation_id}")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["status"] == "completed"
    assert body["returncode"] == 0
    assert "decision accuracy 10/10" in body["output"]
    assert body["report"]["markdown"] == "# 10/10"

    missing = test_client.get("/api/v1/evaluations/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "evaluation_not_found"


def test_document_routes_only_serve_the_allowlisted_slugs(client):
    test_client, _ = client

    architecture = test_client.get("/api/v1/documents/architecture")
    assert architecture.status_code == 200
    body = architecture.json()
    assert body["slug"] == "architecture"
    assert "The thesis" in body["markdown"]

    assumptions = test_client.get("/api/v1/documents/assumptions")
    assert assumptions.status_code == 200
    assert "What is measured vs. modeled" in assumptions.json()["markdown"]

    # A `../` traversal attempt changes the path segment count, so Starlette's router
    # never matches `/api/v1/documents/{slug}` at all (404) — it never reaches our
    # handler or touches the filesystem.
    traversal = test_client.get("/api/v1/documents/..%2f..%2fpyproject")
    assert traversal.status_code == 404

    # A single unknown segment does reach the handler, where FastAPI's `Literal` path
    # param validation rejects anything outside the allowlist before any file access.
    unknown = test_client.get("/api/v1/documents/unknown")
    assert unknown.status_code == 422


def test_document_read_failure_returns_typed_not_found(client, tmp_path, monkeypatch):
    test_client, _ = client
    monkeypatch.setitem(
        document_routes.DOCUMENTS,
        "architecture",
        ("Architecture", tmp_path / "missing.md"),
    )

    response = test_client.get("/api/v1/documents/architecture")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "document_not_found"
