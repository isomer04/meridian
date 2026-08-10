import Link from "next/link";
import type { Decision, RunUpdate } from "@/lib/api/generated";

const labels: Record<Decision, string> = {
  approved: "APPROVED",
  approved_with_conditions: "APPROVED WITH CONDITIONS",
  incomplete: "INCOMPLETE",
  suspended: "SUSPENDED",
  denied: "DENIED",
};

function timestamp(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : null;
}

export function DeterminationBand({ run }: { run: RunUpdate }) {
  const result = run.result;
  if (run.status === "failed")
    return (
      <section className="determination failed" aria-labelledby="determination">
        <p className="eyebrow">RUN FAILURE</p>
        <h1 id="determination">Run failed safely</h1>
        <p>
          {run.error_type ?? "Run failure"}:{" "}
          {run.error_message ||
            "The run stopped before a determination could be produced."}
        </p>
        <p className="muted">
          The loan was not determined. Review live activity for technical
          details, then start a new case when ready.
        </p>
      </section>
    );
  if (run.status === "awaiting_approval") {
    const approval = run.approval;
    const artifactKind =
      typeof approval?.artifact.kind === "string"
        ? approval.artifact.kind.replaceAll("_", " ")
        : "current approval artifact";
    const href = approval
      ? {
          pathname: "/approvals",
          query: { loan_id: approval.loan_id, gate: approval.gate },
        }
      : "/approvals";
    return (
      <section
        className="determination awaiting"
        aria-labelledby="determination"
      >
        <p className="eyebrow">HUMAN AUTHORITY REQUIRED</p>
        <h1 id="determination">Awaiting gate {approval?.gate || "decision"}</h1>
        <p>
          Paused at gate {approval?.gate || ""}. A named reviewer must decide
          the {artifactKind}.
        </p>
        {approval && (
          <p className="mono">Artifact digest: {approval.artifact_digest}</p>
        )}
        <Link className="primary" href={href}>
          Review gate {approval?.gate || ""}
        </Link>
      </section>
    );
  }
  const decision = result?.decision;
  const statusClass = decision ? `decision-${decision}` : "";
  return (
    <section
      className={`determination ${statusClass}`}
      aria-labelledby="determination"
    >
      <p className="eyebrow">DETERMINATION</p>
      <h1 id="determination" className="decision">
        {decision ? labels[decision] : "DETERMINATION PENDING"}
      </h1>
      {result?.expected_decision && (
        <p>
          <strong>
            {result.expected_matches ? "Matches" : "Does not match"} expected{" "}
            {result.expected_decision.replaceAll("_", " ")}.
          </strong>
        </p>
      )}
      <p>
        {result?.decision_rationale ||
          "Running scenario; the determination will appear when the flow reaches a terminal state."}
      </p>
      {timestamp(run.terminal_at) && (
        <p className="muted">
          Terminal state recorded {timestamp(run.terminal_at)}.
        </p>
      )}
    </section>
  );
}
