"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, api } from "@/lib/api/client";
import type { RunRequest, Scenario, SystemState } from "@/lib/api/generated";
import { DocumentIntake } from "@/components/DocumentIntake";

type RunForm = Omit<RunRequest, "scenario_id">;
const defaults: RunForm = {
  judgment_kind: "stub",
  roster: "production",
  policy_attack: false,
  submit_twice: false,
  auto_approve: false,
};

export function RunConfiguration() {
  const router = useRouter();
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [system, setSystem] = useState<SystemState>();
  const [scenarioId, setScenarioId] = useState<number>();
  const [form, setForm] = useState(defaults);
  const [error, setError] = useState<string>();
  const [activeRunId, setActiveRunId] = useState<string>();
  const [pending, setPending] = useState(false);
  const [entryMode, setEntryMode] = useState<"documents" | "demo">("documents");

  useEffect(() => {
    Promise.all([api.scenarios(), api.system()])
      .then(([available, runtime]) => {
        if (runtime.active_run_id) {
          router.replace(`/loans/runs/${runtime.active_run_id}`);
          return;
        }
        setScenarios(available);
        setScenarioId(available[0]?.scenario_id);
        setSystem(runtime);
        if (!(runtime.crew_available ?? runtime.api_key_present))
          setForm((current) => ({ ...current, judgment_kind: "stub" }));
      })
      .catch((reason: unknown) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not load run configuration.",
        ),
      );
  }, [router]);

  const selected = scenarios.find(
    (scenario) => scenario.scenario_id === scenarioId,
  );
  const crewAvailable = (system?.crew_available ?? system?.api_key_present) === true;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!scenarioId) return;
    setPending(true);
    setError(undefined);
    setActiveRunId(undefined);
    try {
      const { run_id } = await api.startRun({
        ...form,
        scenario_id: scenarioId,
      });
      router.push(`/loans/runs/${run_id}`);
    } catch (reason) {
      const apiError = reason as ApiError;
      if (apiError.status === 409) {
        const active = (
          apiError.detail as { active_run_id?: string } | undefined
        )?.active_run_id;
        setActiveRunId(active);
        setError(
          active ? "Another case is already running." : apiError.message,
        );
      } else setError(apiError.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div className="entry-mode" role="group" aria-label="Case source">
        <button type="button" className={entryMode === "documents" ? "selected" : ""} onClick={() => setEntryMode("documents")}>Upload documents</button>
        <button type="button" className={entryMode === "demo" ? "selected" : ""} onClick={() => setEntryMode("demo")}>Use demo scenario</button>
      </div>
      {entryMode === "documents" ? <DocumentIntake /> : (
    <div className="columns">
      <section className="panel" aria-labelledby="briefing">
        <p className="eyebrow">SCENARIO BRIEFING</p>
        <h2 id="briefing">{selected?.name || "Loading scenarios…"}</h2>
        {selected ? (
          <>
            <p>
              <span className="mono">{selected.loan_id}</span> · Expected
              outcome:{" "}
              <strong>
                {selected.expected_decision?.replaceAll("_", " ") ||
                  "not specified"}
              </strong>
            </p>
            <p>
              {selected.summary ||
                "This scenario exercises deterministic control flow and bounded judgment; evidence is retained in the case record."}
            </p>
          </>
        ) : (
          <p>Loading available case scenarios…</p>
        )}
      </section>
      <form
        className="panel"
        onSubmit={submit}
        aria-describedby={error ? "run-error" : undefined}
      >
        <p className="eyebrow">RUN CONFIGURATION</p>
        <h2>Configure a case</h2>
        <div className="form-row">
          <label htmlFor="scenario">Scenario</label>
          <select
            id="scenario"
            value={scenarioId ?? ""}
            onChange={(event) => setScenarioId(Number(event.target.value))}
            required
          >
            {scenarios.map((scenario) => (
              <option key={scenario.scenario_id} value={scenario.scenario_id}>
                {scenario.label}
              </option>
            ))}
          </select>
        </div>
        <fieldset>
          <legend>Judgment mode</legend>
          <label className="checkbox">
            <input
              type="radio"
              name="judgment_kind"
              checked={form.judgment_kind === "stub"}
              onChange={() => setForm({ ...form, judgment_kind: "stub" })}
            />
            Stub judgment
          </label>
          <label className="checkbox">
            <input
              type="radio"
              name="judgment_kind"
              disabled={!crewAvailable}
              checked={form.judgment_kind === "crew"}
              onChange={() => setForm({ ...form, judgment_kind: "crew" })}
            />
            Crew judgment
          </label>
          {!crewAvailable && (
            <p className="muted">
              {system?.crew_setup_action ??
                "Configure credentials for the selected model provider and restart the API."} {" "}
              Stub judgment is ready to run locally.
            </p>
          )}
        </fieldset>
        <div className="form-row">
          <label htmlFor="roster">Crew roster</label>
          <select
            id="roster"
            disabled={form.judgment_kind !== "crew"}
            value={form.roster}
            onChange={(event) =>
              setForm({
                ...form,
                roster: event.target.value as "production" | "demo",
              })
            }
          >
            <option value="production">Production</option>
            <option value="demo">Demo</option>
          </select>
        </div>
        <details>
          <summary>Demonstration controls</summary>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.policy_attack}
              onChange={(event) =>
                setForm({ ...form, policy_attack: event.target.checked })
              }
            />
            Inject a policy-gate attack
          </label>
          <p className="muted">
            Attempts the prohibited appraisal order before disclosures to prove
            the policy control holds.
          </p>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.submit_twice}
              onChange={(event) =>
                setForm({ ...form, submit_twice: event.target.checked })
              }
            />
            Submit twice to inspect idempotency
          </label>
          <p className="muted">
            Replays the same case to demonstrate that one tri-merge inquiry is
            retained.
          </p>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.auto_approve}
              onChange={(event) =>
                setForm({ ...form, auto_approve: event.target.checked })
              }
            />
            Auto-approve human gates
          </label>
          {form.auto_approve && (
            <p className="notice">
              Human gates will be bypassed and attributed to AUTO-APPROVE for
              this run.
            </p>
          )}
        </details>
        {error && (
          <p id="run-error" className="notice error" role="alert">
            {error}
            {activeRunId && (
              <>
                {" "}
                <Link href={`/loans/runs/${activeRunId}`}>View active run</Link>
              </>
            )}
          </p>
        )}
        <button className="primary" disabled={pending || !scenarioId}>
          {pending ? "Starting case…" : "Start case run"}
        </button>
      </form>
    </div>
      )}
    </>
  );
}
