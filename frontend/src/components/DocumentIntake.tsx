"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { FileCheck2, FileUp, ScanLine } from "lucide-react";
import { api, ApiError, type IntakeDraft } from "@/lib/api/client";

const fieldNames = [
  ["borrower.name", "Borrower name"], ["borrower.employer", "Employer"],
  ["borrower.base_annual", "Annual base income"], ["property.address", "Property address"],
  ["property.estimated_value", "Estimated value"], ["loan.purchase_price", "Purchase price"],
  ["loan.loan_amount", "Loan amount"], ["loan.note_rate", "Interest rate"],
  ["loan.term_years", "Term (years)"],
] as const;

function initialValues(draft: IntakeDraft) {
  return Object.fromEntries(fieldNames.map(([key]) => [key, String(draft.proposed_fields[key]?.value ?? "")])) as Record<string, string>;
}

export function DocumentIntake() {
  const router = useRouter();
  const [files, setFiles] = useState<File[]>([]);
  const [draft, setDraft] = useState<IntakeDraft>();
  const [values, setValues] = useState<Record<string, string>>({});
  const [reviewer, setReviewer] = useState("");
  const [ssnOnFile, setSsnOnFile] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!files.length) return;
    setPending(true); setError(undefined);
    try {
      const next = await api.uploadIntake(files);
      setDraft(next);
      setValues(initialValues(next));
      setSsnOnFile(next.proposed_fields["borrower.ssn_on_file"]?.value === true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not process the documents.");
    } finally { setPending(false); }
  }

  async function confirmAndRun(event: React.FormEvent) {
    event.preventDefault(); setError(undefined);
    if (!draft) return;
    const numericFields = [
      ["borrower.base_annual", "Annual base income", 0],
      ["property.estimated_value", "Estimated value", 0],
      ["loan.purchase_price", "Purchase price", 0],
      ["loan.loan_amount", "Loan amount", 0],
      ["loan.note_rate", "Interest rate", 6.5],
      ["loan.term_years", "Term (years)", 30],
    ] as const;
    const numbers: Record<string, number> = {};
    for (const [key, label, fallback] of numericFields) {
      const value = Number(values[key] || fallback);
      if (!Number.isFinite(value)) {
        setError(`${label} must be a finite number.`);
        return;
      }
      numbers[key] = value;
    }
    setPending(true);
    try {
      const caseData = {
        borrower: { name: values["borrower.name"], employer: values["borrower.employer"], base_annual: numbers["borrower.base_annual"], ssn_on_file: ssnOnFile, employment_type: "w2", years_at_employer: 2, reserves_months: 3 },
        property: { address: values["property.address"], estimated_value: numbers["property.estimated_value"], type: "single_family_detached", occupancy: "primary_residence", units: 1, rural: false, in_disaster_area: false },
        loan: { loan_amount: numbers["loan.loan_amount"], purchase_price: numbers["loan.purchase_price"], note_rate: numbers["loan.note_rate"], term_years: numbers["loan.term_years"], program: "conventional_conforming", purpose: "purchase", annual_property_tax: 0, annual_hazard_insurance: 0, monthly_hoa: 0, monthly_mi: 0, subordinate_liens: 0 },
        le_delivered: true, intent_to_proceed: true,
      };
      await api.confirmIntake(draft.intake_id, draft.revision, reviewer, caseData);
      const { run_id } = await api.startIntakeRun(draft.intake_id);
      router.push(`/loans/runs/${run_id}`);
    } catch (reason) {
      const message = reason instanceof ApiError ? reason.message : reason instanceof Error ? reason.message : "Could not start the case.";
      setError(message);
    } finally { setPending(false); }
  }

  if (!draft) return (
    <form className="panel document-upload" onSubmit={upload}>
      <FileUp aria-hidden="true" size={28} />
      <h2>Upload a loan document package</h2>
      <p>Native-text and scanned PDFs are processed locally in Python. No document content is sent to an AI service.</p>
      <label className="file-drop" htmlFor="loan-documents">
        <strong>Select PDF documents</strong><span>Up to 15 MB each and 40 MB total</span>
      </label>
      <input id="loan-documents" type="file" accept="application/pdf,.pdf" multiple required onChange={(event) => setFiles(Array.from(event.target.files ?? []))} />
      {files.length > 0 && <ul className="document-file-list">{files.map((file) => <li key={`${file.name}-${file.size}`}><FileCheck2 aria-hidden="true" size={16} />{file.name}<span>{Math.ceil(file.size / 1024)} KB</span></li>)}</ul>}
      {error && <p className="notice error" role="alert">{error}</p>}
      <button className="primary" disabled={pending || !files.length}>{pending ? "Processing documents…" : "Process documents"}</button>
    </form>
  );

  return (
    <form className="panel intake-review" onSubmit={confirmAndRun}>
      <div className="intake-review-heading"><div><p className="eyebrow">HUMAN REVIEW REQUIRED</p><h2>Confirm extracted case facts</h2></div><span className="ocr-status"><ScanLine aria-hidden="true" size={16} />{draft.ocr_available ? "Local OCR ready" : "OCR unavailable"}</span></div>
      <div className="document-summary">{draft.documents.map((document) => <div key={document.document_id}><strong>{document.filename}</strong><span>{document.page_count} page(s) · {document.pages.some((page) => page.method === "ocr") ? "OCR" : "native text"}</span><code>{document.sha256.slice(0, 16)}…</code></div>)}</div>
      {draft.warnings.map((warning) => <p className="notice" key={warning}>{warning}</p>)}
      <div className="intake-fields">{fieldNames.map(([key, label]) => { const source = draft.proposed_fields[key]; return <div className="form-row" key={key}><label htmlFor={key}>{label}</label><input id={key} inputMode={key.includes("name") || key.includes("address") || key.includes("employer") ? undefined : "decimal"} required={["borrower.name", "property.address", "property.estimated_value", "loan.loan_amount"].includes(key)} value={values[key] ?? ""} onChange={(event) => setValues({ ...values, [key]: event.target.value })} />{source && <small>{source.filename}, page {source.page} · {source.method}</small>}</div>; })}</div>
      <label className="checkbox"><input type="checkbox" checked={ssnOnFile} onChange={(event) => setSsnOnFile(event.target.checked)} />SSN is present in the reviewed application (the full value is not retained)</label>
      <div className="form-row"><label htmlFor="intake-reviewer">Reviewed and confirmed by</label><input id="intake-reviewer" autoComplete="name" required value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></div>
      <p className="notice">Uploaded facts drive the case. Credit, AUS, appraisal, pricing, and verification responses remain local simulated integrations in this version.</p>
      {error && <p className="notice error" role="alert">{error}</p>}
      <button className="primary" disabled={pending}>{pending ? "Starting confirmed case…" : "Confirm facts and start case"}</button>
    </form>
  );
}
