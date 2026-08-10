"""Deterministic PDF intake, review drafts, and decision-package exports."""

from __future__ import annotations

from dataclasses import dataclass
import base64
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import stat
from threading import RLock
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape

import fitz
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image
import pytesseract
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_PACKAGE_BYTES = 40 * 1024 * 1024
MAX_PAGES = 100
MIN_NATIVE_TEXT = 24
DOCUMENT_KEY_ENV = "MERIDIAN_DOCUMENT_KEY"
_NONCE_BYTES = 12


class IntakeError(ValueError):
    pass


class IntakeConflictError(IntakeError):
    pass


class _BorrowerCase(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    name: str = Field(min_length=1)
    base_annual: float = Field(default=0, ge=0)


class _PropertyCase(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    address: str = Field(min_length=1)
    estimated_value: float = Field(gt=0)


class _LoanCase(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    loan_amount: float = Field(gt=0)
    purchase_price: float = Field(default=0, ge=0)
    note_rate: float = Field(default=6.5, gt=0, le=100)
    term_years: int = Field(default=30, gt=0, le=50)


class _ConfirmedCase(BaseModel):
    model_config = ConfigDict(extra="allow")
    borrower: _BorrowerCase
    property: _PropertyCase
    loan: _LoanCase

    @model_validator(mode="after")
    def loan_does_not_exceed_value(self) -> "_ConfirmedCase":
        if self.loan.loan_amount > self.property.estimated_value:
            raise ValueError("loan.loan_amount must not exceed property.estimated_value")
        return self


_manifest_locks_guard = RLock()
_manifest_locks: dict[str, RLock] = {}
_key_locks_guard = RLock()
_key_locks: dict[str, RLock] = {}


def _manifest_lock(directory: Path) -> RLock:
    key = str(directory.resolve())
    with _manifest_locks_guard:
        return _manifest_locks.setdefault(key, RLock())


def _key_lock(root: Path) -> RLock:
    key = str(root.resolve())
    with _key_locks_guard:
        return _key_locks.setdefault(key, RLock())


def _private_mode(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def _validate_private_directory(path: Path) -> None:
    if not path.is_dir():
        raise IntakeError("Document storage must be a directory.")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise IntakeError("Document storage permissions must not allow group or public access.")


def _validate_private_file(path: Path) -> None:
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise IntakeError(f"Sensitive file permissions are unsafe: {path.name}")


def _write_private_bytes(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    _private_mode(path, 0o600)


def _decode_key(encoded: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded.strip().encode("ascii"))
    except Exception as exc:
        raise IntakeError(f"{DOCUMENT_KEY_ENV} must be URL-safe base64.") from exc
    if len(key) != 32:
        raise IntakeError(f"{DOCUMENT_KEY_ENV} must decode to exactly 32 bytes.")
    return key


def _encryption_key(root: Path) -> bytes:
    configured = os.environ.get(DOCUMENT_KEY_ENV)
    if configured:
        return _decode_key(configured)
    with _key_lock(root):
        key_file = root / ".document-key"
        if key_file.exists():
            _validate_private_file(key_file)
            return _decode_key(key_file.read_text(encoding="ascii").strip())
        key = AESGCM.generate_key(bit_length=256)
        _write_private_bytes(key_file, base64.urlsafe_b64encode(key))
        return key


def _encrypt(key: bytes, content: bytes, associated_data: bytes) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, content, associated_data)


def _decrypt(key: bytes, content: bytes, associated_data: bytes) -> bytes:
    if len(content) <= _NONCE_BYTES:
        raise IntakeError("Encrypted document storage is corrupt.")
    try:
        return AESGCM(key).decrypt(content[:_NONCE_BYTES], content[_NONCE_BYTES:], associated_data)
    except Exception as exc:
        raise IntakeError("Encrypted document storage could not be authenticated.") from exc


def storage_root() -> Path:
    configured = os.environ.get("MERIDIAN_DOCUMENT_DIR")
    root = Path(configured) if configured else Path.cwd() / ".meridian-documents"
    existed = root.exists()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not existed:
        _private_mode(root, 0o700)
    _validate_private_directory(root)
    return root


def ocr_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class PageText:
    page: int
    text: str
    method: str


FIELD_RULES: dict[str, tuple[str, ...]] = {
    "borrower.name": (r"(?:borrower\s+name|name)\s*[:\-]\s*([^\n]+)",),
    "borrower.ssn_on_file": (r"(?:ssn|social\s+security\s+number)\s*[:\-]\s*(\d{3}[- ]?\d{2}[- ]?\d{4})\b",),
    "borrower.employer": (r"employer\s*[:\-]\s*([^\n]+)",),
    "borrower.base_annual": (r"(?:base\s+annual|annual\s+income|annual\s+salary)\s*[:\-$ ]+([\d,]+(?:\.\d{1,2})?)",),
    "property.address": (r"property\s+address\s*[:\-]\s*([^\n]+)",),
    "property.estimated_value": (r"estimated\s+value\s*[:\-$ ]+([\d,]+(?:\.\d{1,2})?)",),
    "loan.purchase_price": (r"purchase\s+price\s*[:\-$ ]+([\d,]+(?:\.\d{1,2})?)",),
    "loan.loan_amount": (r"loan\s+amount\s*[:\-$ ]+([\d,]+(?:\.\d{1,2})?)",),
    "loan.note_rate": (r"(?:note\s+rate|interest\s+rate)\s*[:\- ]+([\d.]+)\s*%?",),
    "loan.term_years": (r"(?:loan\s+term|term)\s*[:\- ]+(\d+)\s*(?:years?|yrs?)",),
}
MONEY_FIELDS = {
    "borrower.base_annual", "property.estimated_value", "loan.purchase_price", "loan.loan_amount"
}


def _extract_pages(content: bytes) -> tuple[list[PageText], list[str]]:
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise IntakeError("The file is not a readable PDF.") from exc
    if document.needs_pass:
        raise IntakeError("Encrypted PDFs are not supported.")
    if document.page_count > MAX_PAGES:
        raise IntakeError(f"PDF exceeds the {MAX_PAGES}-page limit.")
    pages: list[PageText] = []
    warnings: list[str] = []
    can_ocr = ocr_available()
    for index, page in enumerate(document):
        native = page.get_text("text").strip()
        if len(native) >= MIN_NATIVE_TEXT:
            pages.append(PageText(index + 1, native, "native"))
            continue
        if not can_ocr:
            pages.append(PageText(index + 1, native, "ocr_unavailable"))
            warnings.append(f"Page {index + 1} has no usable text layer and local OCR is unavailable.")
            continue
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.open(BytesIO(pixmap.tobytes("png")))
        try:
            text = pytesseract.image_to_string(image, timeout=30).strip()
            pages.append(PageText(index + 1, text, "ocr"))
            if not text:
                warnings.append(f"OCR found no text on page {index + 1}.")
        except RuntimeError as exc:
            if "timeout" in str(exc).lower():
                raise IntakeError(f"Local OCR exceeded the 30-second limit on page {index + 1}.") from exc
            pages.append(PageText(index + 1, "", "ocr_failed"))
            warnings.append(f"Local OCR failed on page {index + 1}.")
        except Exception:
            pages.append(PageText(index + 1, "", "ocr_failed"))
            warnings.append(f"Local OCR failed on page {index + 1}.")
    return pages, warnings


def _proposals(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    proposed: dict[str, dict[str, Any]] = {}
    for document in documents:
        for page in document["pages"]:
            for field, patterns in FIELD_RULES.items():
                if field in proposed:
                    continue
                for pattern in patterns:
                    match = re.search(pattern, page["text"], re.IGNORECASE)
                    if not match:
                        continue
                    raw = match.group(1).strip()
                    value: str | float = raw
                    if field == "borrower.ssn_on_file":
                        value = True
                    elif field in MONEY_FIELDS:
                        value = float(raw.replace(",", ""))
                    elif field == "loan.note_rate":
                        value = float(raw)
                    elif field == "loan.term_years":
                        value = float(raw)
                    proposed[field] = {
                        "value": value,
                        "document_id": document["document_id"],
                        "filename": document["filename"],
                        "page": page["page"],
                        "source_text": "SSN: [REDACTED]" if field == "borrower.ssn_on_file" else match.group(0)[:240],
                        "method": f"labeled_fields_v1:{page['method']}",
                    }
                    break
    return proposed


def create_draft(files: list[tuple[str, bytes]]) -> dict[str, Any]:
    if not files:
        raise IntakeError("At least one PDF is required.")
    if sum(len(content) for _, content in files) > MAX_PACKAGE_BYTES:
        raise IntakeError("Document package exceeds the 40 MB limit.")
    intake_id = str(uuid4())
    directory = storage_root() / intake_id
    directory.mkdir(mode=0o700)
    documents: list[dict[str, Any]] = []
    extracted_documents: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        _private_mode(directory, 0o700)
        key = _encryption_key(directory.parent)
        for position, (filename, content) in enumerate(files, start=1):
            if len(content) > MAX_FILE_BYTES:
                raise IntakeError(f"{filename} exceeds the 15 MB file limit.")
            if not content.startswith(b"%PDF-"):
                raise IntakeError(f"{filename} is not a PDF.")
            digest = sha256(content).hexdigest()
            pages, page_warnings = _extract_pages(content)
            document_id = f"doc-{position:03d}-{digest[:12]}"
            object_name = f"{document_id}.pdf.enc"
            original = directory / object_name
            _write_private_bytes(
                original,
                _encrypt(key, content, f"{intake_id}:{document_id}".encode("utf-8")),
            )
            extracted_document = {
                "document_id": document_id,
                "filename": Path(filename).name,
                "sha256": digest,
                "page_count": len(pages),
                "pages": [page.__dict__ for page in pages],
                "storage": {"algorithm": "AES-256-GCM", "object": object_name},
            }
            extracted_documents.append(extracted_document)
            documents.append(extracted_document | {
                "pages": [{"page": page.page, "method": page.method} for page in pages],
            })
            warnings.extend(f"{Path(filename).name}: {warning}" for warning in page_warnings)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    try:
        draft = {
            "intake_id": intake_id,
            "status": "needs_review",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ocr_available": ocr_available(),
            "documents": documents,
            "proposed_fields": _proposals(extracted_documents),
            "warnings": warnings,
            "confirmed_case": None,
            "revision": 1,
            "review_events": [],
        }
        _write_manifest(directory, draft)
        return draft
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _write_manifest(directory: Path, draft: dict[str, Any]) -> None:
    with _manifest_lock(directory):
        pending = directory / f"manifest.{uuid4().hex}.enc.tmp"
        try:
            plaintext = json.dumps(draft, indent=2).encode("utf-8")
            encrypted = _encrypt(_encryption_key(directory.parent), plaintext, directory.name.encode("ascii"))
            _write_private_bytes(pending, encrypted)
            pending.replace(directory / "manifest.json.enc")
            _private_mode(directory / "manifest.json.enc", 0o600)
        finally:
            pending.unlink(missing_ok=True)


def get_draft(intake_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f-]{36}", intake_id):
        raise IntakeError("Invalid intake identifier.")
    root = storage_root()
    manifest = root / intake_id / "manifest.json.enc"
    if not manifest.exists():
        raise KeyError(intake_id)
    _validate_private_file(manifest)
    plaintext = _decrypt(_encryption_key(root), manifest.read_bytes(), intake_id.encode("ascii"))
    return json.loads(plaintext.decode("utf-8"))


def decrypt_original(intake_id: str, document_id: str) -> bytes:
    """Read a stored original for a trusted integration without exposing it to the UI."""
    draft = get_draft(intake_id)
    document = next((item for item in draft["documents"] if item["document_id"] == document_id), None)
    if document is None:
        raise KeyError(document_id)
    root = storage_root()
    path = root / intake_id / document["storage"]["object"]
    _validate_private_file(path)
    return _decrypt(
        _encryption_key(root), path.read_bytes(), f"{intake_id}:{document_id}".encode("utf-8"),
    )


def public_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """Return intake metadata without duplicating full document text to clients."""
    result = json.loads(json.dumps(draft))
    for document in result.get("documents", []):
        document.pop("storage", None)
        for page in document.get("pages", []):
            page.pop("text", None)
    return result


def confirm_draft(intake_id: str, case: dict[str, Any], reviewer: str, expected_revision: int) -> dict[str, Any]:
    reviewer = reviewer.strip()
    if not reviewer:
        raise IntakeError("A named reviewer is required.")
    try:
        validated = _ConfirmedCase.model_validate(case).model_dump()
    except ValidationError as exc:
        message = exc.errors(include_url=False)[0]["msg"]
        location = ".".join(str(part) for part in exc.errors(include_url=False)[0]["loc"])
        raise IntakeError(f"Invalid confirmed case field {location}: {message}") from exc
    directory = storage_root() / intake_id
    with _manifest_lock(directory):
        draft = get_draft(intake_id)
        if draft.get("status") != "needs_review":
            raise IntakeConflictError("This intake has already been confirmed.")
        if draft.get("revision") != expected_revision:
            raise IntakeConflictError("This intake draft changed; refresh it before confirming.")
        reviewed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        revision = expected_revision + 1
        corrected_fields = sorted(
            key for key, source in draft.get("proposed_fields", {}).items()
            if key.split(".", 1)[0] in validated
            and validated[key.split(".", 1)[0]].get(key.split(".", 1)[1]) != source.get("value")
        )
        draft["status"] = "confirmed"
        draft["revision"] = revision
        draft["confirmed_case"] = validated | {"reviewed_by": reviewer, "reviewed_at": reviewed_at}
        draft.setdefault("review_events", []).append({
            "revision": revision,
            "action": "confirmed",
            "reviewed_by": reviewer,
            "reviewed_at": reviewed_at,
            "corrected_fields": corrected_fields,
        })
        _write_manifest(directory, draft)
        return draft


def decision_package_pdf(result: dict[str, Any], source_digests: list[str] | None = None) -> bytes:
    output = BytesIO()
    styles = getSampleStyleSheet()
    body = styles["BodyText"].clone("PackageBody", fontSize=8.5, leading=11)
    label = styles["BodyText"].clone("PackageLabel", fontSize=8, leading=10, textColor=colors.HexColor("#475569"))
    heading = styles["Heading2"].clone("PackageHeading", spaceBefore=14, spaceAfter=7)
    doc = SimpleDocTemplate(
        output,
        pagesize=LETTER,
        title="Meridian decision package",
        invariant=1,
        leftMargin=42,
        rightMargin=42,
        topMargin=42,
        bottomMargin=42,
    )

    def paragraph(value: Any, style=body) -> Paragraph:
        return Paragraph(escape(str(value if value not in (None, "") else "Not recorded")), style)

    def table(headers: list[str], rows: list[list[Any]], widths: list[int]) -> Table:
        rendered_rows = [[paragraph(value) for value in row] for row in rows]
        rendered_headers = [paragraph(value, label) for value in headers]
        result_table = Table([rendered_headers, *rendered_rows], colWidths=widths, repeatRows=1)
        result_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#172033")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return result_table

    story: list[Any] = [Paragraph("MERIDIAN Decision Package", styles["Title"])]
    story += [Paragraph(f"Loan: {escape(str(result.get('loan_id', 'unknown')))}", heading)]
    generated_at = str(result.get("terminal_at") or result.get("completed_at") or "Not recorded")
    summary_rows = [
        ["Decision", str(result.get("decision") or "not determined").replace("_", " ").title()],
        ["Rationale", result.get("decision_rationale")],
        ["Generated", generated_at],
        ["Schema", "meridian-decision-package/v1"],
    ]
    story += [table(["Field", "Details"], summary_rows, [105, 423]), Spacer(1, 6)]

    story.append(Paragraph("Conditions", heading))
    conditions = result.get("conditions") or []
    if conditions:
        story.append(table(
            ["Type", "Requirement", "Source", "Raised by"],
            [[item.get("kind", ""), item.get("description", ""), item.get("citation", ""), item.get("raised_by", "")] for item in conditions],
            [95, 245, 105, 83],
        ))
    else:
        story.append(paragraph("No conditions were recorded."))

    story.append(Paragraph("Approval decisions", heading))
    approvals = result.get("approvals") or []
    if approvals:
        story.append(table(
            ["Gate", "Status", "Approver", "Recorded"],
            [[item.get("gate", ""), item.get("status", ""), item.get("approver", ""), item.get("decided_at") or item.get("requested_at", "")] for item in approvals],
            [80, 110, 180, 158],
        ))
    else:
        story.append(paragraph("No human approval decision was required for this package."))

    story.append(Paragraph("Ledger summary", heading))
    ledger = result.get("ledger") or []
    if ledger:
        story.append(table(
            ["#", "Step", "Phase", "Outcome", "Recorded"],
            [[item.get("id", ""), item.get("step", ""), item.get("phase", ""), item.get("outcome", ""), datetime.fromtimestamp(float(item["timestamp"]), timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if item.get("timestamp") else ""] for item in ledger],
            [32, 190, 78, 70, 158],
        ))
    else:
        story.append(paragraph("No ledger entries were recorded."))

    if source_digests:
        story.append(Paragraph("Source document digests", heading))
        story.append(table(["SHA-256 digest"], [[digest] for digest in source_digests], [528]))
    doc.build(story)
    return output.getvalue()
