from io import BytesIO
import json
import sqlite3
import threading
import time

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from meridian.api.app import create_app
from meridian.api.dependencies import get_run_coordinator
from meridian.application.run_coordinator import RunCoordinator
from meridian.core import db as db_mod
from meridian.core.events import BUS


def _pdf() -> bytes:
    output = BytesIO()
    page = canvas.Canvas(output)
    for index, line in enumerate((
        "Borrower Name: Alex Morgan", "Employer: Northwind Logistics",
        "Annual Income: $132,000", "Property Address: 1420 Alder St, Boise, ID 83702",
        "Estimated Value: $500,000", "Purchase Price: $500,000", "Loan Amount: $400,000",
        "Interest Rate: 6.5%", "Loan Term: 30 years",
    )):
        page.drawString(72, 740 - index * 24, line)
    page.save()
    return output.getvalue()


def test_confirmed_pdf_intake_runs_and_exports_package(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path / "documents"))
    database = db_mod.Database(tmp_path / "intakes.db")
    db_mod.set_db(database)
    app = create_app()
    coordinator = RunCoordinator()
    app.dependency_overrides[get_run_coordinator] = lambda: coordinator
    with TestClient(app) as client:
        upload = client.post("/api/v1/intakes", files=[("files", ("application.pdf", _pdf(), "application/pdf"))])
        assert upload.status_code == 201
        intake_id = upload.json()["intake_id"]
        case = {
            "borrower": {"name": "Alex Morgan", "ssn_on_file": True, "employer": "Northwind Logistics", "base_annual": 132000, "employment_type": "w2"},
            "property": {"address": "1420 Alder St", "estimated_value": 500000, "type": "single_family_detached", "occupancy": "primary_residence"},
            "loan": {"loan_amount": 400000, "purchase_price": 500000, "note_rate": 6.5, "term_years": 30, "program": "conventional_conforming", "purpose": "purchase"},
        }
        confirmed = client.post(f"/api/v1/intakes/{intake_id}/confirm", json={"expected_revision": upload.json()["revision"], "reviewer": "Case Reviewer", "case": case})
        assert confirmed.status_code == 200
        started = client.post(f"/api/v1/intakes/{intake_id}/runs", json={"auto_approve": True})
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        deadline = time.monotonic() + 30
        while not coordinator.is_terminal(run_id):
            assert time.monotonic() < deadline
            time.sleep(.01)
        snapshot = client.get(f"/api/v1/runs/{run_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["scenario"]["name"] == "Confirmed PDF intake (simulated vendors)"
        assert snapshot.json()["status"] == "completed"
        app.dependency_overrides[get_run_coordinator] = lambda: RunCoordinator()
        from meridian.api.routes import intakes

        monkeypatch.setattr(intakes, "get_draft", lambda _intake_id: (_ for _ in ()).throw(AssertionError("package must use the stored digests")))
        package = client.get(f"/api/v1/runs/{run_id}/decision-package.pdf")
        assert package.status_code == 200
        assert package.headers["content-disposition"].startswith("attachment;")
        assert package.content.startswith(b"%PDF-")
    record = database.load_intake_run_record(run_id)
    assert record["intake_id"] == intake_id
    assert record["source_document_digests"] == [upload.json()["documents"][0]["sha256"]]
    database.close()
    db_mod.set_db(None)
    BUS.clear()


def test_intake_rejects_non_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/intakes", files=[("files", ("notes.txt", b"not pdf", "text/plain"))])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "intake_error"


def test_legacy_completed_intake_record_recovers_source_digests(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-intakes.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE intake_run_records (
            run_id TEXT PRIMARY KEY,
            intake_id TEXT NOT NULL,
            result_json TEXT NOT NULL,
            terminal_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO intake_run_records VALUES (?, ?, ?, ?)",
        ("legacy-run", "legacy-intake", json.dumps({"loan_id": "MER-LEGACY"}), "2026-08-10T00:00:00Z"),
    )
    connection.commit()
    connection.close()

    database = db_mod.Database(db_path)
    db_mod.set_db(database)
    assert database.load_intake_run_record("legacy-run")["source_document_digests"] is None

    from meridian.api.routes import intakes

    captured: list[list[str]] = []
    monkeypatch.setattr(
        intakes,
        "get_draft",
        lambda _intake_id: {"documents": [{"sha256": "legacy-source-digest"}]},
    )
    monkeypatch.setattr(
        intakes,
        "decision_package_pdf",
        lambda _result, digests: captured.append(digests) or b"%PDF-legacy",
    )
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/runs/legacy-run/decision-package.pdf")

    assert response.status_code == 200
    assert captured == [["legacy-source-digest"]]
    database.close()
    db_mod.set_db(None)


def test_upload_parsing_runs_off_the_event_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    request_thread = threading.get_ident()
    parsing_threads: list[int] = []
    from meridian.api.routes import intakes

    real_create_draft = intakes.create_draft

    def observed_create_draft(payloads):
        parsing_threads.append(threading.get_ident())
        return real_create_draft(payloads)

    monkeypatch.setattr(intakes, "create_draft", observed_create_draft)
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/intakes",
            files=[("files", ("application.pdf", _pdf(), "application/pdf"))],
        )

    assert response.status_code == 201
    assert parsing_threads and parsing_threads[0] != request_thread
