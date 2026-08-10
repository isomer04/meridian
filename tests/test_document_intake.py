from io import BytesIO
from hashlib import sha256
import base64
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

import fitz
import pytest
from reportlab.pdfgen import canvas

from meridian.application import document_intake


DEMO_DOCUMENTS = Path(__file__).resolve().parents[1] / "output" / "pdf"


def labeled_pdf() -> bytes:
    output = BytesIO()
    page = canvas.Canvas(output)
    lines = [
        "Borrower Name: Alex Morgan",
        "SSN: 900-12-3456",
        "Employer: Northwind Logistics",
        "Annual Income: $132,000",
        "Property Address: 1420 Alder St, Boise, ID 83702",
        "Estimated Value: $500,000",
        "Purchase Price: $500,000",
        "Loan Amount: $400,000",
        "Interest Rate: 6.5%",
        "Loan Term: 30 years",
    ]
    for index, line in enumerate(lines):
        page.drawString(72, 740 - index * 24, line)
    page.save()
    return output.getvalue()


def test_native_pdf_proposes_fields_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    draft = document_intake.create_draft([("application.pdf", labeled_pdf())])

    assert draft["status"] == "needs_review"
    assert draft["documents"][0]["pages"][0]["method"] == "native"
    amount = draft["proposed_fields"]["loan.loan_amount"]
    assert amount["value"] == 400000
    assert amount["page"] == 1
    assert amount["document_id"].startswith("doc-")
    intake_directory = tmp_path / draft["intake_id"]
    encrypted_original = intake_directory / f"{amount['document_id']}.pdf.enc"
    assert encrypted_original.exists()
    assert not encrypted_original.read_bytes().startswith(b"%PDF-")
    decrypted = document_intake.decrypt_original(draft["intake_id"], amount["document_id"])
    assert decrypted.startswith(b"%PDF-")
    assert sha256(decrypted).hexdigest() == draft["documents"][0]["sha256"]
    manifest = intake_directory / "manifest.json.enc"
    assert manifest.exists()
    assert b"900-12-3456" not in manifest.read_bytes()
    assert not (intake_directory / "manifest.json").exists()
    assert all("text" not in page for page in draft["documents"][0]["pages"])
    ssn = draft["proposed_fields"]["borrower.ssn_on_file"]
    assert ssn["value"] is True
    assert ssn["source_text"] == "SSN: [REDACTED]"
    assert "900-12-3456" not in str(ssn)


def test_image_only_pdf_reports_missing_local_ocr(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    monkeypatch.setattr(document_intake, "ocr_available", lambda: False)
    pdf = fitz.open()
    pdf.new_page()
    draft = document_intake.create_draft([("scan.pdf", pdf.tobytes())])

    assert draft["documents"][0]["pages"][0]["method"] == "ocr_unavailable"
    assert "local OCR is unavailable" in draft["warnings"][0]


def test_ocr_timeout_rejects_and_cleans_up_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    monkeypatch.setattr(document_intake, "ocr_available", lambda: True)
    monkeypatch.setattr(
        document_intake.pytesseract,
        "image_to_string",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Tesseract process timeout")),
    )
    pdf = fitz.open()
    pdf.new_page()

    with pytest.raises(document_intake.IntakeError, match="30-second limit"):
        document_intake.create_draft([("scan.pdf", pdf.tobytes())])

    assert list(tmp_path.glob("*/manifest.json.enc")) == []


def test_confirmation_requires_named_reviewer_and_required_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    draft = document_intake.create_draft([("application.pdf", labeled_pdf())])
    case = {
        "borrower": {"name": "Alex Morgan", "ssn_on_file": True, "base_annual": 132000},
        "property": {"address": "1420 Alder St", "estimated_value": 500000},
        "loan": {"loan_amount": 400000, "purchase_price": 500000},
    }
    confirmed = document_intake.confirm_draft(draft["intake_id"], case, "Case Reviewer", draft["revision"])
    assert confirmed["status"] == "confirmed"
    assert confirmed["revision"] == draft["revision"] + 1
    assert confirmed["confirmed_case"]["reviewed_by"] == "Case Reviewer"
    assert confirmed["review_events"][-1]["action"] == "confirmed"


def test_invalid_and_encrypted_inputs_fail_safely(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    try:
        document_intake.create_draft([("not.pdf", b"not a pdf")])
    except document_intake.IntakeError as exc:
        assert "not a PDF" in str(exc)
    else:
        raise AssertionError("non-PDF input was accepted")

    encrypted = fitz.open()
    encrypted.new_page()
    content = encrypted.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret",
    )
    try:
        document_intake.create_draft([("encrypted.pdf", content)])
    except document_intake.IntakeError as exc:
        assert "Encrypted PDFs" in str(exc)
    else:
        raise AssertionError("encrypted PDF input was accepted")


def test_decision_package_is_a_pdf():
    result = {
        "loan_id": "MER-1",
        "decision": "approved",
        "decision_rationale": "Meets <requirements>",
        "terminal_at": "2026-08-09T20:00:00Z",
        "conditions": [{"kind": "prior_to_closing", "description": "Verify income", "citation": "DU-1", "raised_by": "underwriter"}],
        "ledger": [{"id": 1, "step": "pull_credit", "phase": "execute", "outcome": "ok", "timestamp": 1_786_133_825}],
    }
    content = document_intake.decision_package_pdf(
        result,
        ["a" * 64],
    )
    assert content.startswith(b"%PDF-")
    assert len(content) > 1000
    assert content == document_intake.decision_package_pdf(result, ["a" * 64])
    text = "".join(page.get_text() for page in fitz.open(stream=content, filetype="pdf"))
    assert "Ledger summary" in text
    assert "pull_credit" in text
    assert '"step"' not in text


def test_confirmation_rejects_stale_revision_and_invalid_case(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    draft = document_intake.create_draft([("application.pdf", labeled_pdf())])
    case = {
        "borrower": {"name": "Alex Morgan", "base_annual": 132000},
        "property": {"address": "1420 Alder St", "estimated_value": 500000},
        "loan": {"loan_amount": 600000, "purchase_price": 500000},
    }
    try:
        document_intake.confirm_draft(draft["intake_id"], case, "Reviewer", draft["revision"])
    except document_intake.IntakeError as exc:
        assert "loan.loan_amount" in str(exc)
    else:
        raise AssertionError("invalid cross-field values were accepted")

    case["loan"]["loan_amount"] = 400000
    document_intake.confirm_draft(draft["intake_id"], case, "Reviewer", draft["revision"])
    try:
        document_intake.confirm_draft(draft["intake_id"], case, "Other", draft["revision"])
    except document_intake.IntakeConflictError:
        pass
    else:
        raise AssertionError("a repeated confirmation was accepted")


def test_permanent_demo_pdfs_exercise_native_and_scanned_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    native = (DEMO_DOCUMENTS / "meridian-demo-native-application.pdf").read_bytes()
    native_draft = document_intake.create_draft([("native.pdf", native)])
    assert native_draft["documents"][0]["pages"][0]["method"] == "native"
    assert native_draft["proposed_fields"]["borrower.ssn_on_file"]["value"] is True
    assert "900-12-3456" not in str(native_draft["proposed_fields"])

    scanned = (DEMO_DOCUMENTS / "meridian-demo-scanned-application.pdf").read_bytes()
    scanned_draft = document_intake.create_draft([("scanned.pdf", scanned)])
    method = scanned_draft["documents"][0]["pages"][0]["method"]
    assert method in {"ocr", "ocr_unavailable", "ocr_failed"}
    if method == "ocr":
        assert scanned_draft["proposed_fields"]["loan.loan_amount"]["value"] == 400000
        assert scanned_draft["proposed_fields"]["borrower.ssn_on_file"]["value"] is True


def test_configured_document_key_keeps_key_out_of_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    monkeypatch.setenv("MERIDIAN_DOCUMENT_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    draft = document_intake.create_draft([("application.pdf", labeled_pdf())])
    assert not (tmp_path / ".document-key").exists()

    monkeypatch.setenv("MERIDIAN_DOCUMENT_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    try:
        document_intake.get_draft(draft["intake_id"])
    except document_intake.IntakeError as exc:
        assert "authenticated" in str(exc)
    else:
        raise AssertionError("manifest decrypted with the wrong key")


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("borrower", "base_annual"),
        ("property", "estimated_value"),
        ("loan", "loan_amount"),
    ],
)
def test_confirmation_rejects_non_finite_numbers(tmp_path, monkeypatch, section, field):
    monkeypatch.setenv("MERIDIAN_DOCUMENT_DIR", str(tmp_path))
    draft = document_intake.create_draft([("application.pdf", labeled_pdf())])
    case = {
        "borrower": {"name": "Alex Morgan", "base_annual": 132000},
        "property": {"address": "1420 Alder St", "estimated_value": 500000},
        "loan": {"loan_amount": 400000, "purchase_price": 500000},
    }
    case[section][field] = float("inf")

    with pytest.raises(document_intake.IntakeError, match=field):
        document_intake.confirm_draft(
            draft["intake_id"], case, "Case Reviewer", draft["revision"],
        )


def test_concurrent_key_initialization_returns_one_complete_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MERIDIAN_DOCUMENT_KEY", raising=False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _: document_intake._encryption_key(tmp_path), range(16)))

    assert len(set(keys)) == 1
    assert len(keys[0]) == 32
    assert document_intake._decode_key((tmp_path / ".document-key").read_text()) == keys[0]
