import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { RunConfiguration } from "./RunConfiguration";
const push = vi.fn();
const replace = vi.fn();
const router = { push, replace };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({
  api: { scenarios: vi.fn(), system: vi.fn(), startRun: vi.fn() },
}));
import { api } from "@/lib/api/client";
describe("RunConfiguration", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("resumes the active run when returning to the new-loan page", async () => {
    vi.mocked(api.scenarios).mockResolvedValue([]);
    vi.mocked(api.system).mockResolvedValue({
      version: "0.1.0",
      api_key_present: true,
      default_judgment: "crew",
      crew_available: true,
      active_run_id: "crew-run-1",
    });

    render(<RunConfiguration />);

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/loans/runs/crew-run-1"),
    );
  });

  it("reveals auto-approval warning and starts selected scenario", async () => {
    vi.mocked(api.scenarios).mockResolvedValue([
      {
        scenario_id: 1,
        label: "1. Clean",
        name: "Clean W-2",
        loan_id: "MER-1001",
        expected_decision: "approved",
        summary: "Happy path.",
      },
    ]);
    vi.mocked(api.system).mockResolvedValue({
      version: "0.1.0",
      api_key_present: false,
      default_judgment: "stub",
      crew_available: false,
      crew_setup_action: "Set DEEPSEEK_API_KEY and restart the API.",
    });
    vi.mocked(api.startRun).mockResolvedValue({ run_id: "r-1" });
    render(<RunConfiguration />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Use demo scenario" }));
    await screen.findByText("Clean W-2");
    expect(
      screen.getByText(/Set DEEPSEEK_API_KEY and restart the API/),
    ).toBeInTheDocument();
    await user.click(screen.getByText("Demonstration controls"));
    await user.click(screen.getByLabelText("Auto-approve human gates"));
    expect(
      screen.getByText(/Human gates will be bypassed/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start case run" }));
    await waitFor(() =>
      expect(api.startRun).toHaveBeenCalledWith(
        {
          scenario_id: 1,
          judgment_kind: "stub",
          roster: "production",
          policy_attack: false,
          submit_twice: false,
          auto_approve: true,
        },
      ),
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith("/loans/runs/r-1"));
  });

  it("is operable entirely from the keyboard", async () => {
    vi.mocked(api.scenarios).mockResolvedValue([
      {
        scenario_id: 1,
        label: "1. Clean",
        name: "Clean W-2",
        loan_id: "MER-1001",
        expected_decision: "approved",
        summary: "Happy path.",
      },
    ]);
    vi.mocked(api.system).mockResolvedValue({
      version: "0.1.0",
      api_key_present: true,
      default_judgment: "stub",
      crew_available: true,
    });
    vi.mocked(api.startRun).mockResolvedValue({ run_id: "r-2" });
    render(<RunConfiguration />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Use demo scenario" }));
    await screen.findByText("Clean W-2");
    const scenarioSelect = screen.getByLabelText("Scenario");
    scenarioSelect.focus();
    expect(scenarioSelect).toHaveFocus();
    const stubRadio = screen.getByRole("radio", { name: "Stub judgment" });
    await user.tab();
    expect(stubRadio).toHaveFocus();
    const crewRadio = screen.getByRole("radio", { name: "Crew judgment" });
    await user.keyboard("{ArrowRight}");
    expect(crewRadio).toHaveFocus();
    expect(crewRadio).toBeChecked();
    await user.tab();
    expect(screen.getByLabelText("Crew roster")).toHaveFocus();
    await user.tab();
    expect(screen.getByText("Demonstration controls")).toHaveFocus();
    await user.tab();
    await user.tab();
    await user.tab();
    await user.tab();
    const submit = screen.getByRole("button", { name: "Start case run" });
    expect(submit).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/loans/runs/r-2"));
  });

  it("opens demonstration controls and toggles a checkbox via the keyboard", async () => {
    vi.mocked(api.scenarios).mockResolvedValue([
      {
        scenario_id: 1,
        label: "1. Clean",
        name: "Clean W-2",
        loan_id: "MER-1001",
        expected_decision: "approved",
        summary: "Happy path.",
      },
    ]);
    vi.mocked(api.system).mockResolvedValue({
      version: "0.1.0",
      api_key_present: false,
      default_judgment: "stub",
      crew_available: false,
    });
    render(<RunConfiguration />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Use demo scenario" }));
    await screen.findByText("Clean W-2");
    const summary = screen.getByText("Demonstration controls");
    await user.click(summary);
    expect(summary.closest("details")).toHaveAttribute("open");
    const policyAttack = screen.getByLabelText("Inject a policy-gate attack");
    await user.tab();
    expect(policyAttack).toHaveFocus();
    await user.keyboard(" ");
    expect(policyAttack).toBeChecked();
  });
});
