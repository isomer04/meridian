"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api/client";
import { connectRunEvents, type StreamState } from "@/lib/api/events";
import type { EventRecord, LoanResult, RunUpdate } from "@/lib/api/generated";
import { DeterminationBand } from "./DeterminationBand";

const stamp = (value: string) => new Date(value).toLocaleTimeString();
const eventKind = (event: EventRecord) => event.kind ?? event.type;

type ConditionsResult = Pick<
  LoanResult,
  "overlays" | "value_acceptance" | "conditions"
>;

function ConditionsAndExceptions({ result }: { result: ConditionsResult }) {
  const overlays = result.overlays ?? [];
  const conditions = result.conditions ?? [];
  const valueAcceptance = result.value_acceptance ?? {
    offered: false,
    rationale: "",
  };
  return (
    <section className="section">
      <h2>Conditions and exceptions</h2>
      {overlays.length > 0 && (
        <div className="evidence">
          <table>
            <caption>Overlay conflicts</caption>
            <thead><tr><th>Dimension</th><th>Agency</th><th>Overlay</th><th>Disposition</th></tr></thead>
            <tbody>
              {overlays.map((overlay, index) => (
                <tr key={index}>
                  <td>{overlay.dimension}</td>
                  <td>{String(overlay.agency ?? "—")}</td>
                  <td>{String(overlay.overlay ?? "—")} {overlay.citation}</td>
                  <td>{overlay.exception_granted ? "Exception granted" : "Overlay binds"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p>
        <strong>Value acceptance:</strong>{" "}
        {valueAcceptance.offered
          ? valueAcceptance.exercised ? "exercised" : "offered, not exercised"
          : "not offered"}
        . {valueAcceptance.rationale}
      </p>
      {conditions.length > 0 && (
        <div className="evidence">
          <table><caption>Conditions</caption><tbody>
            {conditions.map((condition, index) => (
              <tr key={index}>
                <th scope="row">{condition.kind}</th>
                <td>{condition.description}</td>
                <td>{condition.citation ?? "—"}</td>
                <td>{condition.raised_by}</td>
              </tr>
            ))}
          </tbody></table>
        </div>
      )}
    </section>
  );
}

type AgentPerformanceResult = Pick<LoanResult, "agent_runs" | "idempotency">;

function AgentPerformance({ result }: { result: AgentPerformanceResult }) {
  const agentRuns = result.agent_runs ?? [];
  const idempotency = result.idempotency ?? { enabled: false };
  return (
    <section className="section">
      <h2>Agent performance</h2>
      <div className="evidence"><table>
        <caption>Per-agent cost and latency</caption>
        <thead><tr><th>Agent</th><th>Task</th><th>Seconds</th><th>Cost</th><th>Tools called</th><th>Replay</th></tr></thead>
        <tbody>{agentRuns.map((agent, index) => (
          <tr key={index}>
            <td>{agent.agent}</td><td>{agent.task}</td>
            <td className="mono">{agent.seconds}</td><td className="mono">${agent.usd}</td>
            <td>{(agent.tools_called ?? []).join(", ") || "—"}</td>
            <td>{agent.replayed ? "Replayed" : "New"}</td>
          </tr>
        ))}</tbody>
      </table></div>
      {idempotency.enabled && (
        <p><strong>Idempotency evidence.</strong> Two full submissions made{" "}
          {idempotency.calls_after_second_run} tri-merge calls; before/after the second submission:{" "}
          {idempotency.calls_before_second_run}/{idempotency.calls_after_second_run}.
        </p>
      )}
    </section>
  );
}

function Evidence({ result }: { result: LoanResult }) {
  const cycle = result.cycle_time;
  const citations = result.citations ?? [];
  const guidelineFindings = result.guideline_findings ?? [];
  const controlsFired = result.controls_fired ?? [];
  const qcFindings = result.qc_findings ?? [];
  const ledger = result.ledger ?? [];
  const compensation = result.compensation ?? [];
  const notices = result.notices ?? [];
  return (
    <div className="case-record">
      <ConditionsAndExceptions result={result} />
      <section className="section">
        <h2>Evidence and citations</h2>
        <div className="evidence">
          <table>
            <caption>
              Citation verification —{" "}
              {citations.filter((citation) => citation.verified).length}/
              {citations.length} verified
            </caption>
            <thead>
              <tr>
                <th>Source</th>
                <th>Claim</th>
                <th>Verification</th>
              </tr>
            </thead>
            <tbody>
              {citations.map((citation, index) => (
                <tr key={index}>
                  <td>
                    {citation.corpus} {citation.section}
                  </td>
                  <td>{citation.claim}</td>
                  <td>{citation.verified ? "Verified" : "Requires review"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {guidelineFindings.map((finding, index) => (
          <p key={index}>
            <strong>Retrieval:</strong> {finding.question} —{" "}
            {finding.sufficient ? "sufficient" : "insufficient"};{" "}
            {(finding.citations ?? []).join(", ")}
          </p>
        ))}
      </section>
      <section className="section">
        <h2>Controls and QC</h2>
        {controlsFired.map((control) => (
          <p className="severity critical" key={control}>
            Policy control held: {control}
          </p>
        ))}
        {qcFindings.map((finding, index) => (
          <p
            className={`severity ${finding.severity.toLowerCase()}`}
            key={index}
          >
            <strong>
              {finding.severity} · {finding.kind}
            </strong>{" "}
            — {finding.detail}
          </p>
        ))}
      </section>
      <section className="section">
        <h2>Ledger and compensation</h2>
        <div className="evidence">
          <table>
            <caption>Saga ledger</caption>
            <thead>
              <tr>
                <th>ID</th>
                <th>Step</th>
                <th>Phase</th>
                <th>Outcome</th>
                <th>Idempotency key</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((entry) => (
                <tr key={entry.id}>
                  <td className="mono">{entry.id}</td>
                  <td>{entry.step}</td>
                  <td>{entry.phase}</td>
                  <td>{entry.outcome}</td>
                  <td className="mono">{entry.idempotency_key ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {compensation.length > 0 && (
          <>
            <h3>Compensation unwound in reverse</h3>
            {compensation.map((entry, index) => (
              <p key={index}>
                <strong>{entry.step}</strong> — {entry.outcome}: {entry.note}
              </p>
            ))}
          </>
        )}
      </section>
      <section className="section">
        <h2>Notices and cycle time</h2>
        {notices.map((notice, index) => (
          <p key={index}>
            <strong>{notice.kind}</strong> issued {notice.issued_on}, due{" "}
            {notice.due_on}; {notice.citation}; {(notice.reasons ?? []).join("; ")}
          </p>
        ))}
        {cycle && (
          <div className="evidence">
            <table>
              <caption>Cycle-time model</caption>
              <tbody>
                {Object.entries(cycle).map(([key, value]) => (
                  <tr key={key}>
                    <th scope="row">{key.replaceAll("_", " ")}</th>
                    <td className="mono">
                      {Array.isArray(value) ? value.join(", ") : String(value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <AgentPerformance result={result} />
    </div>
  );
}

function EventStream({
  events,
  trace,
  status,
}: {
  events: EventRecord[];
  trace: string[];
  status: string;
}) {
  const [filter, setFilter] = useState("all");
  const [newActivity, setNewActivity] = useState(false);
  const [copyState, setCopyState] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const visible = useMemo(
    () =>
      events
        .filter((event) => filter === "all" || eventKind(event).startsWith(filter))
        .slice(-500),
    [events, filter],
  );
  const categories = [
    ...new Set(events.map((event) => eventKind(event).split(".")[0])),
  ];
  const lastVisibleEventId = visible.at(-1)?.id;

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const nearBottom =
      list.scrollHeight - list.scrollTop - list.clientHeight < 32;
    if (nearBottom) list.scrollTop = list.scrollHeight;
    else setNewActivity(true);
  }, [visible.length, lastVisibleEventId]);

  async function copyTrace() {
    try {
      await navigator.clipboard.writeText(trace.join("\n"));
      setCopyState("Raw trace copied.");
    } catch {
      setCopyState(
        "Could not copy the trace. Select the activity rows manually.",
      );
    }
  }

  function showLatest() {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
    setNewActivity(false);
  }

  return (
    <aside className="panel" aria-label="Live activity">
      <p className="eyebrow">LIVE ACTIVITY</p>
      <p aria-live="polite">
        {status === "running"
          ? "Running case; live activity is updating."
          : `Run ${status.replaceAll("_", " ")}.`}
      </p>
      <label className="compact">
        Filter activity{" "}
        <select
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="all">All categories</option>
          {categories.map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
        </select>
      </label>
      <button className="secondary" onClick={copyTrace}>
        Copy raw trace
      </button>
      {copyState && (
        <p className="muted" role="status">
          {copyState}
        </p>
      )}
      {newActivity && (
        <button className="secondary new-activity" onClick={showLatest}>
          New activity — show latest
        </button>
      )}
      <div
        className="event-list"
        ref={listRef}
        onScroll={() => {
          const list = listRef.current;
          if (
            list &&
            list.scrollHeight - list.scrollTop - list.clientHeight < 32
          )
            setNewActivity(false);
        }}
      >
        {visible.map((event, index) => (
          <article className="event" key={event.id ?? `${event.timestamp}-${index}`}>
            <header>
              <span>{stamp(event.timestamp)}</span>
              <span>{event.actor}</span>
              <span>{eventKind(event)}</span>
            </header>
            <div>{event.line}</div>
            {Object.keys(event.payload).length > 0 && (
              <details>
                <summary>Show technical detail</summary>
                <pre>{JSON.stringify(event.payload, null, 2)}</pre>
              </details>
            )}
          </article>
        ))}
      </div>
      {events.length > 500 && (
        <p className="muted">
          Showing the latest 500 retained events. The retained trace remains
          available to copy.
        </p>
      )}
    </aside>
  );
}

export function CaseWorkspace({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunUpdate>();
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [streamState, setStreamState] = useState<StreamState | "loading">(
    "loading",
  );
  const [message, setMessage] = useState("");

  useEffect(() => {
    let stop = () => {};
    let disposed = false;
    async function load() {
      try {
        const initial = await api.run(runId);
        if (disposed) return;
        setRun(initial);
        setEvents(initial.events);
        if (initial.status === "running") {
          const latestId = initial.events.at(-1)?.id ?? 0;
          stop = connectRunEvents(
            runId,
            latestId,
            (event) => {
              setEvents((current) => {
                const duplicate =
                  event.id !== undefined &&
                  current.some(
                    (existing) =>
                      existing.id !== undefined && existing.id === event.id,
                  );
                return duplicate ? current : [...current, event];
              });
              if (eventKind(event) === "run.terminal") {
                stop();
                api
                  .run(runId)
                  .then((snapshot) => {
                    if (!disposed) setRun(snapshot);
                  })
                  .catch(() => {
                    if (!disposed) {
                      setStreamState("error");
                      setMessage(
                        "Live updates could not be read. Refresh this case to recover the latest state.",
                      );
                    }
                  });
              }
            },
            (state) => {
              if (!disposed) {
                setStreamState(state);
                if (state === "reconnecting") {
                  setMessage(
                    "Live updates interrupted. Reconnecting; the Python run continues.",
                  );
                } else if (state === "error") {
                  setMessage(
                    "Live updates could not be read. Refresh this case to recover the latest state.",
                  );
                } else {
                  setMessage("");
                }
              }
            },
          );
        } else setStreamState("open");
      } catch (error) {
        if (!disposed) {
          setStreamState("error");
          setMessage(
            error instanceof ApiError && error.status === 404
              ? "This case run was not found. Start a new run."
              : error instanceof Error
                ? error.message
                : "Could not load this case run.",
          );
        }
      }
    }
    void load();
    return () => {
      disposed = true;
      stop();
    };
  }, [runId]);

  if (!run)
    return (
      <>
        <h1>Case run</h1>
        <p
          className={streamState === "error" ? "notice error" : "notice"}
          role={streamState === "error" ? "alert" : undefined}
        >
          {message || "Loading case record…"}
        </p>
      </>
    );
  const displayEvents = events.length ? events : run.events;
  const trace = run.trace_lines.length
    ? run.trace_lines
    : displayEvents.map((event) => event.line);
  return (
    <>
      <p className="eyebrow">
        {run.scenario
          ? `${run.scenario.loan_id} / SCENARIO ${run.scenario.scenario_id}`
          : `CASE RUN ${run.run_id}`}
      </p>
      <DeterminationBand run={run} />
      {run.status === "completed" && run.result && (
        <p className="decision-package-action">
          <a className="primary" href={api.decisionPackageUrl?.(run.run_id) ?? `/api/v1/runs/${run.run_id}/decision-package.pdf`} download>
            Download decision package PDF
          </a>
          <span>The structured ledger remains the authoritative case record.</span>
        </p>
      )}
      {streamState === "reconnecting" && (
        <p className="notice" role="status">
          {message}
        </p>
      )}
      {streamState === "error" && (
        <p className="notice error" role="alert">
          {message}
        </p>
      )}
      <div className="case-grid">
        <div>
          {run.result ? (
            <Evidence result={run.result} />
          ) : (
            <section className="panel">
              <h2>Case record</h2>
              <p>
                The structured determination will be available when this run
                reaches a terminal state.
              </p>
            </section>
          )}
        </div>
        <EventStream events={displayEvents} trace={trace} status={run.status} />
      </div>
    </>
  );
}
