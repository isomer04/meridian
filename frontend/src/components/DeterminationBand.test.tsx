import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DeterminationBand } from "./DeterminationBand";
import type { RunUpdate } from "@/lib/api/generated";

const base = {
  run_id: "run-3",
  scenario: {
    scenario_id: 3,
    label: "3",
    name: "Low value",
    loan_id: "MER-1003",
  },
  mode: "stub",
  events: [],
  trace_lines: [],
};
const completed: RunUpdate = {
  ...base,
  status: "completed",
  result: {
    loan_id: "MER-1003",
    scenario_name: "Low value",
    decision: "denied",
    decision_rationale: "Binding overlay",
    expected_decision: "denied",
    expected_matches: true,
    overlays: [],
    value_acceptance: { offered: false, rationale: "" },
    conditions: [],
    citations: [],
    qc_findings: [],
    controls_fired: [],
    guideline_findings: [],
    ledger: [],
    compensation: [],
    notices: [],
    agent_runs: [],
    tri_merge_calls: 0,
    idempotency: { enabled: false },
  },
};

describe("DeterminationBand", () => {
  afterEach(() => cleanup());
  it("states a denied result and expected comparison", () => {
    render(<DeterminationBand run={completed} />);
    expect(screen.getByRole("heading", { name: "DENIED" })).toBeInTheDocument();
    expect(screen.getByText(/Matches expected denied/i)).toBeInTheDocument();
  });
  it("makes an approval action explicit", () => {
    const awaiting: RunUpdate = {
      ...base,
      status: "awaiting_approval",
      approval: {
        loan_id: "MER-1003",
        gate: "G1",
        status: "pending",
        artifact_digest: "x",
        artifact: {},
        requested_at: 1,
      },
    };
    render(<DeterminationBand run={awaiting} />);
    expect(
      screen.getByRole("link", { name: "Review gate G1" }),
    ).toBeInTheDocument();
  });
});
