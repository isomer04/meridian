import { RunConfiguration } from "@/components/RunConfiguration";
export default function NewLoanPage() {
  return (
    <>
      <p className="eyebrow">RUN A LOAN</p>
      <h1 className="page-title">New underwriting case</h1>
      <p className="lead">
        Upload and verify a loan document package, or use a bounded demonstration
        scenario. The deterministic orchestrator keeps control flow auditable.
      </p>
      <RunConfiguration />
    </>
  );
}
