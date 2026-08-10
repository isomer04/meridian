import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CaseWorkspace } from "./CaseWorkspace";

vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
      public detail?: unknown,
    ) {
      super(message);
    }
  },
  api: { run: vi.fn() },
}));
vi.mock("@/lib/api/events", () => ({
  connectRunEvents: vi.fn(() => () => {}),
}));

import { api } from "@/lib/api/client";
import { connectRunEvents } from "@/lib/api/events";

const completedRun = {
  run_id: "run-1",
  status: "completed" as const,
  mode: "stub",
  scenario: {
    scenario_id: 1,
    label: "1. Clean W-2",
    name: "Clean W-2",
    loan_id: "MER-1001",
    expected_decision: "approved",
  },
  events: [
    {
      id: 1,
      run_id: "run-1",
      type: "flow.step",
      actor: "OriginationFlow",
      loan_id: "MER-1001",
      timestamp: "2026-08-07T12:00:00Z",
      payload: {},
      line: "flow.step intake",
    },
  ],
  trace_lines: ["flow.step intake"],
  terminal_at: "2026-08-07T12:00:01Z",
  result: {
    loan_id: "MER-1001",
    scenario_name: "Clean W-2",
    decision: "approved" as const,
    decision_rationale: "The file meets the documented requirements.",
    expected_decision: "approved",
    expected_matches: true,
    overlays: [],
    value_acceptance: {
      offered: true,
      exercised: true,
      rationale: "Value acceptance exercised.",
    },
    conditions: [],
    citations: [],
    qc_findings: [],
    controls_fired: [],
    guideline_findings: [],
    ledger: [],
    compensation: [],
    notices: [],
    agent_runs: [],
    tri_merge_calls: 1,
    idempotency: { enabled: false },
  },
};

describe("CaseWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the API run snapshot as a structured completed case record", async () => {
    vi.mocked(api.run).mockResolvedValue(completedRun);
    render(<CaseWorkspace runId="run-1" />);
    expect(
      await screen.findByRole("heading", { name: "APPROVED" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/MER-1001/)).toBeInTheDocument();
    expect(
      screen.getByText("Citation verification — 0/0 verified"),
    ).toBeInTheDocument();
    expect(screen.getByText("Per-agent cost and latency")).toBeInTheDocument();
    expect(screen.getByText(/Terminal state recorded/)).toBeInTheDocument();
  });

  it("refreshes the determination after a terminal stream event", async () => {
    const runningRun = {
      ...completedRun,
      status: "running" as const,
      result: undefined,
      terminal_at: undefined,
    };
    vi.mocked(api.run)
      .mockResolvedValueOnce(runningRun)
      .mockResolvedValueOnce(completedRun);
    render(<CaseWorkspace runId="run-1" />);
    await screen.findByRole("heading", { name: "DETERMINATION PENDING" });

    const onEvent = vi.mocked(connectRunEvents).mock.calls[0][2];
    onEvent({
      id: 2,
      run_id: "run-1",
      type: "run.terminal",
      actor: "RunCoordinator",
      loan_id: "MER-1001",
      timestamp: "2026-08-07T12:00:01Z",
      payload: {},
      line: "run.terminal completed",
    });

    expect(
      await screen.findByRole("heading", { name: "APPROVED" }),
    ).toBeInTheDocument();
    expect(api.run).toHaveBeenCalledTimes(2);
  });

  it("shows a visible notice when the event stream cannot be read", async () => {
    vi.mocked(api.run).mockResolvedValue({
      ...completedRun,
      status: "running",
      result: undefined,
      terminal_at: undefined,
    });
    render(<CaseWorkspace runId="run-1" />);
    await screen.findByRole("heading", { name: "DETERMINATION PENDING" });

    const onStatus = vi.mocked(connectRunEvents).mock.calls[0][3];
    onStatus("error");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Live updates could not be read/,
      ),
    );
  });
});
