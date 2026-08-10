"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, ArrowRight, CheckCircle2, ChevronRight, Clock3, FileJson, Inbox, UserCheck, XCircle } from "lucide-react";
import { ApiError, api } from "@/lib/api/client";
import type { Approval } from "@/lib/api/generated";

const GATE_LABELS: Record<string, string> = {
  G1: "GATE G1 — Denial",
  G2: "GATE G2 — Overlay exception",
  G3: "GATE G3 — Value acceptance decline",
  G4: "GATE G4 — QC failure",
};

function gateLabel(gate: string) { return GATE_LABELS[gate] ?? `GATE ${gate}`; }

function artifactSummary(artifact: Record<string, unknown>): string {
  const kind = typeof artifact.kind === "string" ? artifact.kind : "artifact";
  switch (kind) {
    case "denial": return `Denial — ${Array.isArray(artifact.reasons) ? artifact.reasons.length : 0} adverse-action reason(s)`;
    case "overlay_exception": return `Overlay exception — ${Array.isArray(artifact.conflicts) ? artifact.conflicts.length : 0} conflict(s) granted`;
    case "decline_value_acceptance": return `Decline value acceptance — $${artifact.cost_usd ?? "?"} / ~${artifact.estimated_delay_days ?? "?"} days`;
    case "qc_failure": return `QC failure — ${Array.isArray(artifact.qc_findings) ? artifact.qc_findings.length : 0} finding(s)`;
    default: return kind.replaceAll("_", " ");
  }
}

function waitLabel(requestedAt: number) {
  const seconds = Math.max(0, Date.now() / 1000 - requestedAt);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.round(minutes / 60)} h`;
}

function ArtifactFields({ artifact }: { artifact: Record<string, unknown> }) {
  const kind = typeof artifact.kind === "string" ? artifact.kind : "artifact";
  return (
    <dl className="artifact-fields">
      <div><dt>Artifact kind</dt><dd>{kind.replaceAll("_", " ")}</dd></div>
      {Object.entries(artifact).filter(([key]) => key !== "kind").map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>{Array.isArray(value) ? (value.length ? value.map((entry, index) => <div key={index} className="mono">{typeof entry === "object" ? JSON.stringify(entry) : String(entry)}</div>) : "none") : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function DecisionPanel({ item, onDecided }: { item: Approval; onDecided: (message: string) => Promise<void> }) {
  const [decision, setDecision] = useState<"approved" | "rejected">("approved");
  const [approver, setApprover] = useState("");
  const [approverError, setApproverError] = useState<string>();
  const [conflictMessage, setConflictMessage] = useState<string>();
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!approver.trim()) { setApproverError("A named approver is required."); return; }
    setApproverError(undefined); setConflictMessage(undefined); setPending(true);
    try {
      await api.decideApproval(item.loan_id, item.gate, { artifact_digest: item.artifact_digest, decision, approver: approver.trim() });
      await onDecided(`Decision recorded for ${item.loan_id} / ${item.gate}. Rerun the case to resume from the durable ledger.`);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) setConflictMessage("This artifact changed after the queue loaded. Refresh and review the current version before deciding.");
      else if (reason instanceof ApiError && reason.status === 422) setApproverError(reason.message);
      else setConflictMessage(reason instanceof Error ? reason.message : "Could not record the decision.");
    } finally { setPending(false); }
  }

  return (
    <form className="decision-workspace" onSubmit={submit} aria-labelledby="decision-panel">
      <div className="case-overview">
        <div>
          <p className="eyebrow">Case overview</p>
          <h2 id="decision-panel"><span className="mono">{item.loan_id}</span> / <span className="mono">{item.gate}</span></h2>
          <p className="muted">{artifactSummary(item.artifact)}</p>
        </div>
        <span className="awaiting-badge"><Clock3 aria-hidden="true" size={14} /> Awaiting gate decision</span>
      </div>
      <dl className="case-metadata">
        <div><dt>Artifact digest</dt><dd className="mono">{item.artifact_digest}</dd></div>
        <div><dt>Requested</dt><dd className="mono">{new Date(item.requested_at * 1000).toLocaleString()}</dd></div>
        <div><dt>Gate</dt><dd>{gateLabel(item.gate)}</dd></div>
        <div><dt>Review urgency</dt><dd className="warning-text">Waiting {waitLabel(item.requested_at)}</dd></div>
      </dl>
      <ArtifactFields artifact={item.artifact} />
      <details className="raw-artifact">
        <summary><ChevronRight aria-hidden="true" size={18} /><FileJson aria-hidden="true" size={18} /> Review raw artifact JSON</summary>
        <pre tabIndex={0} aria-label="Raw artifact JSON">{JSON.stringify(item.artifact, null, 2)}</pre>
      </details>
      <h3 className="decision-heading">Record a gate decision</h3>
      <fieldset className="decision-fieldset">
        <legend>Decision</legend>
        <div className="decision-options">
          <label className={`decision-option approve ${decision === "approved" ? "selected" : ""}`}>
            <input type="radio" name="decision" aria-label="Approved" checked={decision === "approved"} onChange={() => setDecision("approved")} />
            <CheckCircle2 aria-hidden="true" /><span><strong>Approved</strong><small>Authorize this gate artifact</small></span>
          </label>
          <label className={`decision-option reject ${decision === "rejected" ? "selected" : ""}`}>
            <input type="radio" name="decision" aria-label="Rejected" checked={decision === "rejected"} onChange={() => setDecision("rejected")} />
            <XCircle aria-hidden="true" /><span><strong>Rejected</strong><small>Decline this gate artifact</small></span>
          </label>
        </div>
      </fieldset>
      <div className="form-row approver-field">
        <label htmlFor="approver">Named approver</label>
        <div className="input-with-icon"><UserCheck aria-hidden="true" size={18} /><input id="approver" value={approver} onChange={(event) => setApprover(event.target.value)} aria-invalid={approverError ? true : undefined} aria-describedby={approverError ? "approver-error" : undefined} autoComplete="name" /></div>
        {approverError && <p id="approver-error" className="notice error" role="alert">{approverError}</p>}
      </div>
      {conflictMessage && <p className="notice error" role="alert">{conflictMessage}</p>}
      <div className="decision-actions">
        <p><AlertTriangle aria-hidden="true" size={18} /><span>This decision is durable. The loan must be rerun to resume from the ledger.</span></p>
        <button className="primary" disabled={pending}>{pending ? "Recording…" : "Record gate decision"}<ArrowRight aria-hidden="true" size={17} /></button>
      </div>
    </form>
  );
}

export function ApprovalWorkbench() {
  const searchParams = useSearchParams();
  const [approvals, setApprovals] = useState<Approval[]>();
  const [error, setError] = useState<string>();
  const [selectionOverride, setSelectionOverride] = useState<string>();
  const [confirmation, setConfirmation] = useState<string>();

  function load() {
    return api.approvals().then((items) => { setApprovals(items); setError(undefined); return items; }).catch((reason: unknown) => { setError(reason instanceof Error ? reason.message : "Could not load the approval queue."); return undefined; });
  }
  useEffect(() => { void load(); }, []);
  const selectedKey = useMemo(() => {
    if (!approvals?.length) return undefined;
    if (selectionOverride && approvals.some((item) => `${item.loan_id}:${item.gate}` === selectionOverride)) return selectionOverride;
    const requestedLoan = searchParams.get("loan_id"); const requestedGate = searchParams.get("gate");
    const requested = requestedLoan && requestedGate ? approvals.find((item) => item.loan_id === requestedLoan && item.gate === requestedGate) : undefined;
    const target = requested ?? approvals[0]; return `${target.loan_id}:${target.gate}`;
  }, [approvals, searchParams, selectionOverride]);
  const selected = useMemo(() => approvals?.find((item) => `${item.loan_id}:${item.gate}` === selectedKey), [approvals, selectedKey]);
  async function handleDecided(message: string) { setConfirmation(message); setSelectionOverride(undefined); await load(); }

  if (error) return <section className="approval-state"><h1 tabIndex={-1}>Human gate workbench</h1><p className="notice error" role="alert">{error}</p><button className="secondary" onClick={() => void load()}>Retry</button></section>;
  if (!approvals) return <section className="approval-state"><h1 tabIndex={-1}>Human gate workbench</h1><p role="status">Loading the approval queue…</p></section>;
  const oldestWait = approvals[0] ? waitLabel(approvals[0].requested_at) : null;

  return (
    <div className="approvals-page">
      <header className="approvals-heading">
        <div><p className="eyebrow">Approval queue</p><h1 tabIndex={-1}>Human gate workbench</h1></div>
        <p className="queue-summary">{approvals.length === 0 ? "No approvals are waiting." : `${approvals.length} pending — oldest waiting ${oldestWait}.`}</p>
      </header>
      {confirmation && <p className="notice confirmation" role="status">{confirmation}</p>}
      {approvals.length === 0 ? (
        <section className="empty-approvals"><Inbox aria-hidden="true" size={30} /><h2>Queue clear</h2><p>Runs paused at gates G1–G4 will appear here with the exact artifact that requires a named decision.</p></section>
      ) : (
        <div className="approval-layout">
          <section className="approval-inbox" aria-labelledby="queue-heading">
            <div className="inbox-heading"><div><p className="eyebrow">Review queue</p><h2 id="queue-heading">Pending gates</h2></div><span className="mono">Oldest: {oldestWait}</span></div>
            <ul className="approval-list">
              {approvals.map((item) => { const key = `${item.loan_id}:${item.gate}`; const isSelected = key === selectedKey; return (
                <li key={key}>
                  <button type="button" className={`approval-card ${isSelected ? "selected" : ""}`} aria-label={`Review ${item.loan_id} ${item.gate}`} aria-pressed={isSelected} onClick={() => setSelectionOverride(key)}>
                    <span className="approval-card-top"><span className="mono">{item.loan_id}</span><span className={`gate-badge gate-${item.gate.toLowerCase()}`}>{item.gate}</span></span>
                    <strong>{artifactSummary(item.artifact)}</strong>
                    <span className="approval-card-meta"><Clock3 aria-hidden="true" size={14} /> Waiting {waitLabel(item.requested_at)}<span className="mono">{new Date(item.requested_at * 1000).toLocaleString()}</span></span>
                  </button>
                </li>
              ); })}
            </ul>
          </section>
          {selected && <DecisionPanel key={`${selected.loan_id}:${selected.gate}:${selected.artifact_digest}`} item={selected} onDecided={handleDecided} />}
        </div>
      )}
    </div>
  );
}
