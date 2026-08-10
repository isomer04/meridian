"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api/client";
import type { EvaluationReport, EvaluationStatusResponse } from "@/lib/api/generated";
import { MarkdownDocument } from "@/components/MarkdownDocument";

const POLL_MS = 1200;

function StatusLine({ evaluation }: { evaluation: EvaluationStatusResponse }) {
  if (evaluation.status === "running")
    return <p role="status">Running the eval harness…</p>;
  if (evaluation.status === "failed")
    return (
      <p className="notice error" role="alert">
        Eval harness exited with status {evaluation.returncode ?? "unknown"}.{" "}
        {evaluation.error_message}
      </p>
    );
  return (
    <p className="notice" role="status">
      Eval harness completed (exit {evaluation.returncode}).
    </p>
  );
}

export function EvaluationDashboard() {
  const [report, setReport] = useState<EvaluationReport>();
  const [reportError, setReportError] = useState<string>();
  const [evaluation, setEvaluation] = useState<EvaluationStatusResponse>();
  const [startError, setStartError] = useState<string>();
  const [pollError, setPollError] = useState<string>();
  const [pending, setPending] = useState(false);
  const pollRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const reportVersionRef = useRef(0);

  const loadReport = useCallback(() => {
    const requestedVersion = reportVersionRef.current;
    return api
      .evaluationReport()
      .then((value) => {
        if (reportVersionRef.current !== requestedVersion) return;
        setReport(value);
        setReportError(undefined);
      })
      .catch((reason: unknown) => {
        if (reportVersionRef.current !== requestedVersion) return;
        setReportError(
          reason instanceof Error
            ? reason.message
            : "Could not load the evaluation report.",
        );
      });
  }, []);

  useEffect(() => {
    void loadReport();
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [loadReport]);

  const poll = useCallback(
    (evaluationId: string) => {
      const tick = () => {
        api
          .evaluation(evaluationId)
          .then((snapshot) => {
            setEvaluation(snapshot);
            setPollError(undefined);
            if (snapshot.status === "running") {
              pollRef.current = setTimeout(tick, POLL_MS);
              return;
            }
            setPending(false);
            if (snapshot.report) {
              reportVersionRef.current += 1;
              setReport(snapshot.report);
              setReportError(undefined);
            }
            else void loadReport();
          })
          .catch((reason: unknown) => {
            setPending(false);
            setPollError(
              reason instanceof Error
                ? reason.message
                : "Could not refresh evaluation status.",
            );
          });
      };
      tick();
    },
    [loadReport],
  );

  async function runHarness() {
    setStartError(undefined);
    setPollError(undefined);
    setPending(true);
    try {
      const { evaluation_id } = await api.startEvaluation();
      setEvaluation({
        evaluation_id,
        status: "running",
        output: "",
        started_at: new Date().toISOString(),
      });
      poll(evaluation_id);
    } catch (reason) {
      setPending(false);
      if (reason instanceof ApiError && reason.status === 409) {
        setStartError(
          "An evaluation run is already active. Wait for it to finish before starting another.",
        );
      } else
        setStartError(
          reason instanceof Error
            ? reason.message
            : "Could not start the eval harness.",
        );
    }
  }

  return (
    <>
      <p className="eyebrow">EVALUATION</p>
      <h1 className="page-title">Evaluation evidence</h1>
      <p className="lead">
        How do you know it works? Ten golden-set files, five metrics, plus
        cost and latency per agent. Read the caveat at the top of the report
        before believing the headline numbers.
      </p>
      <section className="panel">
        <h2>Run the eval harness</h2>
        <p>
          Runs <span className="mono">evals/run_evals.py</span> as a bounded
          subprocess. Only one evaluation run is active at a time.
        </p>
        <button className="primary" onClick={runHarness} disabled={pending}>
          {pending ? "Running…" : "Run the eval harness"}
        </button>
        {startError && (
          <p className="notice error" role="alert">
            {startError}
          </p>
        )}
        {pollError && evaluation?.status === "running" && (
          <div className="notice error" role="alert">
            <p>{pollError}</p>
            <button
              className="secondary"
              onClick={() => {
                setPollError(undefined);
                setPending(true);
                poll(evaluation.evaluation_id);
              }}
            >
              Retry evaluation status
            </button>
          </div>
        )}
        {evaluation && (
          <div className="section">
            <StatusLine evaluation={evaluation} />
            {evaluation.output && (
              <details open={evaluation.status !== "completed"}>
                <summary>Harness output</summary>
                <pre className="mono harness-output" tabIndex={0} role="region" aria-label="Harness output log">
                  {evaluation.output}
                </pre>
              </details>
            )}
          </div>
        )}
      </section>
      <section className="panel section" aria-labelledby="report-heading">
        <h2 id="report-heading">Evaluation report</h2>
        {reportError && (
          <p className="notice error" role="alert">
            {reportError}
          </p>
        )}
        {!report && !reportError && (
          <p role="status">Loading the evaluation report…</p>
        )}
        {report && !report.exists && (
          <p className="notice">
            No evaluation report exists yet. Generate one with{" "}
            <span className="mono">uv run python evals/run_evals.py</span>, or
            run the harness above.
          </p>
        )}
        {report && report.exists && <MarkdownDocument markdown={report.markdown} />}
      </section>
    </>
  );
}
