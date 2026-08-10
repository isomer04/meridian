import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { EvaluationDashboard } from "./EvaluationDashboard";

vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
    }
  },
  api: {
    evaluationReport: vi.fn(),
    startEvaluation: vi.fn(),
    evaluation: vi.fn(),
  },
}));
import { ApiError, api } from "@/lib/api/client";

describe("EvaluationDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the explicit absent-report state without inventing metrics", async () => {
    vi.mocked(api.evaluationReport).mockResolvedValue({
      exists: false,
      markdown: "No evaluation report exists yet.",
      path: "/tmp/report.md",
    });
    render(<EvaluationDashboard />);
    expect(
      await screen.findByText(/No evaluation report exists yet/),
    ).toBeInTheDocument();
  });

  it("renders a present report as Markdown with the self-consistency caveat visible", async () => {
    vi.mocked(api.evaluationReport).mockResolvedValue({
      exists: true,
      markdown:
        "# Evaluation report\n\n> Read this before believing the numbers: self-consistency.\n\n| Decision accuracy | Citation validity |\n|---|---|\n| 100% | 100% |",
      path: "/tmp/report.md",
      modified_at: 1700000000,
    });
    render(<EvaluationDashboard />);
    expect(
      await screen.findByText(/self-consistency/),
    ).toBeInTheDocument();
    expect(screen.getByText("Decision accuracy")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("starts the harness, polls to completion, and shows the refreshed report", async () => {
    vi.mocked(api.evaluationReport).mockResolvedValue({
      exists: false,
      markdown: "No evaluation report exists yet.",
      path: "/tmp/report.md",
    });
    vi.mocked(api.startEvaluation).mockResolvedValue({
      evaluation_id: "eval-1",
      status: "running",
    });
    vi.mocked(api.evaluation)
      .mockResolvedValueOnce({
        evaluation_id: "eval-1",
        status: "running",
        output: "starting...",
        started_at: "2024-01-01T00:00:00Z",
      })
      .mockResolvedValueOnce({
        evaluation_id: "eval-1",
        status: "completed",
        returncode: 0,
        output: "decision accuracy 10/10",
        started_at: "2024-01-01T00:00:00Z",
        terminal_at: "2024-01-01T00:00:05Z",
        report: {
          exists: true,
          markdown: "# 10/10",
          path: "/tmp/report.md",
          modified_at: 1700000005,
        },
      });

    const user = userEvent.setup();
    render(<EvaluationDashboard />);
    await screen.findByText(/No evaluation report exists yet/);

    await user.click(
      screen.getByRole("button", { name: "Run the eval harness" }),
    );

    await waitFor(
      () =>
        expect(screen.getByText(/completed \(exit 0\)/)).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(await screen.findByText("10/10")).toBeInTheDocument();
  });

  it("preserves a completed report when the initial report request resolves late", async () => {
    let resolveInitial!: (report: {
      exists: boolean;
      markdown: string;
      path: string;
    }) => void;
    vi.mocked(api.evaluationReport).mockReturnValue(
      new Promise((resolve) => {
        resolveInitial = resolve;
      }),
    );
    vi.mocked(api.startEvaluation).mockResolvedValue({
      evaluation_id: "eval-late",
      status: "running",
    });
    vi.mocked(api.evaluation).mockResolvedValue({
      evaluation_id: "eval-late",
      status: "completed",
      returncode: 0,
      output: "done",
      started_at: "2024-01-01T00:00:00Z",
      terminal_at: "2024-01-01T00:00:05Z",
      report: {
        exists: true,
        markdown: "# Fresh completed report",
        path: "/tmp/report.md",
      },
    });

    const user = userEvent.setup();
    render(<EvaluationDashboard />);
    await user.click(screen.getByRole("button", { name: "Run the eval harness" }));
    expect(await screen.findByText("Fresh completed report")).toBeInTheDocument();

    resolveInitial({
      exists: true,
      markdown: "# Stale initial report",
      path: "/tmp/report.md",
    });
    await waitFor(() =>
      expect(screen.queryByText("Stale initial report")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Fresh completed report")).toBeInTheDocument();
  });

  it("offers a retry when evaluation polling fails", async () => {
    vi.mocked(api.evaluationReport).mockResolvedValue({
      exists: false,
      markdown: "No evaluation report exists yet.",
      path: "/tmp/report.md",
    });
    vi.mocked(api.startEvaluation).mockResolvedValue({
      evaluation_id: "eval-retry",
      status: "running",
    });
    vi.mocked(api.evaluation)
      .mockRejectedValueOnce(new Error("Status request failed"))
      .mockResolvedValueOnce({
        evaluation_id: "eval-retry",
        status: "completed",
        returncode: 0,
        output: "done",
        started_at: "2024-01-01T00:00:00Z",
        terminal_at: "2024-01-01T00:00:05Z",
      });

    const user = userEvent.setup();
    render(<EvaluationDashboard />);
    await screen.findByText(/No evaluation report exists yet/);
    await user.click(screen.getByRole("button", { name: "Run the eval harness" }));
    expect(await screen.findByRole("button", { name: "Retry evaluation status" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Retry evaluation status" }));
    expect(await screen.findByText(/completed \(exit 0\)/)).toBeInTheDocument();
  });

  it("surfaces a 409 as an active-evaluation notice instead of retrying", async () => {
    vi.mocked(api.evaluationReport).mockResolvedValue({
      exists: false,
      markdown: "No evaluation report exists yet.",
      path: "/tmp/report.md",
    });
    vi.mocked(api.startEvaluation).mockRejectedValue(
      new ApiError(409, "another evaluation run is already active"),
    );

    const user = userEvent.setup();
    render(<EvaluationDashboard />);
    await screen.findByText(/No evaluation report exists yet/);
    await user.click(
      screen.getByRole("button", { name: "Run the eval harness" }),
    );

    expect(
      await screen.findByText(/already active/),
    ).toBeInTheDocument();
  });
});
