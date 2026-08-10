import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { ApprovalWorkbench } from "./ApprovalWorkbench";

const searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => searchParams }));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
    }
  },
  api: { approvals: vi.fn(), decideApproval: vi.fn() },
}));
import { ApiError, api } from "@/lib/api/client";

const g1: import("@/lib/api/generated").Approval = {
  loan_id: "MER-1003",
  gate: "G1",
  status: "pending",
  artifact_digest: "digest-g1",
  artifact: { kind: "denial", reasons: ["low appraisal"], qc_findings: [] },
  requested_at: Date.now() / 1000 - 600,
};
const g2: import("@/lib/api/generated").Approval = {
  loan_id: "MER-1002",
  gate: "G2",
  status: "pending",
  artifact_digest: "digest-g2",
  artifact: {
    kind: "overlay_exception",
    conflicts: [{ dimension: "dti" }],
    rationale: "compensating factors",
  },
  requested_at: Date.now() / 1000 - 60,
};

const approvalForGate = (gate: "G3" | "G4") => ({
  ...g1,
  loan_id: `MER-${gate}`,
  gate,
  artifact_digest: `digest-${gate.toLowerCase()}`,
  artifact: { kind: gate === "G3" ? "decline_value_acceptance" : "qc_failure" },
});

describe("ApprovalWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    searchParams.forEach((_value, key) => searchParams.delete(key));
  });

  it("shows the empty-queue explanation when nothing is pending", async () => {
    vi.mocked(api.approvals).mockResolvedValue([]);
    render(<ApprovalWorkbench />);
    expect(
      await screen.findByText(/No approvals are waiting/),
    ).toBeInTheDocument();
  });

  it("preselects the oldest pending item and labels its gate", async () => {
    vi.mocked(api.approvals).mockResolvedValue([g1, g2]);
    render(<ApprovalWorkbench />);
    await screen.findByText("2 pending — oldest waiting 10 min.");
    expect(
      screen.getByRole("heading", { name: "Record a gate decision" }),
    ).toBeInTheDocument();
    expect(screen.getByText("GATE G1 — Denial")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Review MER-1003/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("selects a queued row deep-linked by loan_id and gate", async () => {
    searchParams.set("loan_id", "MER-1002");
    searchParams.set("gate", "G2");
    vi.mocked(api.approvals).mockResolvedValue([g1, g2]);
    render(<ApprovalWorkbench />);
    expect(
      await screen.findByText("GATE G2 — Overlay exception"),
    ).toBeInTheDocument();
  });

  it("selects another inbox record for review", async () => {
    vi.mocked(api.approvals).mockResolvedValue([g1, g2]);
    render(<ApprovalWorkbench />);
    await screen.findByText("GATE G1 — Denial");
    await userEvent.click(screen.getByRole("button", { name: /Review MER-1002/ }));
    expect(screen.getByText("GATE G2 — Overlay exception")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Review MER-1002/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it.each([
    ["G3", "GATE G3 — Value acceptance decline"],
    ["G4", "GATE G4 — QC failure"],
  ] as const)("labels the %s approval gate", async (gate, label) => {
    vi.mocked(api.approvals).mockResolvedValue([approvalForGate(gate)]);
    render(<ApprovalWorkbench />);
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it("rejects a blank approver before calling the API", async () => {
    vi.mocked(api.approvals).mockResolvedValue([g1]);
    render(<ApprovalWorkbench />);
    await screen.findByText("GATE G1 — Denial");
    await userEvent.click(
      screen.getByRole("button", { name: "Record gate decision" }),
    );
    expect(
      await screen.findByText("A named approver is required."),
    ).toBeInTheDocument();
    expect(api.decideApproval).not.toHaveBeenCalled();
  });

  it("records an approved decision and refreshes the queue", async () => {
    vi.mocked(api.approvals)
      .mockResolvedValueOnce([g1])
      .mockResolvedValueOnce([]);
    vi.mocked(api.decideApproval).mockResolvedValue(undefined);
    render(<ApprovalWorkbench />);
    await screen.findByText("GATE G1 — Denial");
    await userEvent.type(
      screen.getByLabelText("Named approver"),
      "Avery Reviewer",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Record gate decision" }),
    );
    await waitFor(() =>
      expect(api.decideApproval).toHaveBeenCalledWith("MER-1003", "G1", {
        artifact_digest: "digest-g1",
        decision: "approved",
        approver: "Avery Reviewer",
      }),
    );
    expect(
      await screen.findByText(/Decision recorded for MER-1003 \/ G1/),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/No approvals are waiting/),
    ).toBeInTheDocument();
  });

  it("keeps the decision control disabled until the queue refresh completes", async () => {
    let finishRefresh!: (items: import("@/lib/api/generated").Approval[]) => void;
    const refresh = new Promise<import("@/lib/api/generated").Approval[]>((resolve) => {
      finishRefresh = resolve;
    });
    vi.mocked(api.approvals).mockResolvedValueOnce([g1]).mockReturnValueOnce(refresh);
    vi.mocked(api.decideApproval).mockResolvedValue(undefined);
    render(<ApprovalWorkbench />);
    await screen.findByRole("heading", { name: "Record a gate decision" });
    await userEvent.type(screen.getByLabelText("Named approver"), "Avery Reviewer");
    await userEvent.click(screen.getByRole("button", { name: "Record gate decision" }));

    expect(screen.getByRole("button", { name: /^Recording/ })).toBeDisabled();
    finishRefresh([]);
    expect(await screen.findByText(/No approvals are waiting/)).toBeInTheDocument();
  });

  it("records a rejected decision when selected", async () => {
    vi.mocked(api.approvals).mockResolvedValue([g1]);
    vi.mocked(api.decideApproval).mockResolvedValue(undefined);
    render(<ApprovalWorkbench />);
    await screen.findByText("GATE G1 — Denial");
    await userEvent.click(screen.getByRole("radio", { name: "Rejected" }));
    await userEvent.type(
      screen.getByLabelText("Named approver"),
      "Morgan Reviewer",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Record gate decision" }),
    );
    await waitFor(() =>
      expect(api.decideApproval).toHaveBeenCalledWith("MER-1003", "G1", {
        artifact_digest: "digest-g1",
        decision: "rejected",
        approver: "Morgan Reviewer",
      }),
    );
  });

  it("surfaces a stale-artifact conflict without recording a decision", async () => {
    vi.mocked(api.approvals).mockResolvedValue([g1]);
    vi.mocked(api.decideApproval).mockRejectedValue(new ApiError(409, "stale"));
    render(<ApprovalWorkbench />);
    await screen.findByText("GATE G1 — Denial");
    await userEvent.type(
      screen.getByLabelText("Named approver"),
      "Avery Reviewer",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Record gate decision" }),
    );
    expect(
      await screen.findByText(/This artifact changed after the queue loaded/),
    ).toBeInTheDocument();
  });
});
