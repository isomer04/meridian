import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentIntake } from "./DocumentIntake";
import { api } from "@/lib/api/client";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: { uploadIntake: vi.fn(), confirmIntake: vi.fn(), startIntakeRun: vi.fn() },
}));

describe("DocumentIntake", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("uploads PDFs, exposes provenance for review, and starts only after confirmation", async () => {
    vi.mocked(api.uploadIntake).mockResolvedValue({
      intake_id: "draft-1", status: "needs_review", revision: 1, ocr_available: true, warnings: [],
      documents: [{ document_id: "doc-1", filename: "application.pdf", sha256: "a".repeat(64), page_count: 1, pages: [{ page: 1, method: "native" }] }],
      proposed_fields: {
        "borrower.name": { value: "Alex Morgan", filename: "application.pdf", page: 1, method: "labeled_fields_v1:native", source_text: "Borrower Name: Alex Morgan" },
        "borrower.ssn_on_file": { value: true, filename: "application.pdf", page: 1, method: "labeled_fields_v1:native", source_text: "SSN: [REDACTED]" },
        "property.address": { value: "1420 Alder St", filename: "application.pdf", page: 1, method: "labeled_fields_v1:native", source_text: "Property Address: 1420 Alder St" },
        "property.estimated_value": { value: 500000, filename: "application.pdf", page: 1, method: "labeled_fields_v1:native", source_text: "Estimated Value: 500000" },
        "loan.loan_amount": { value: 400000, filename: "application.pdf", page: 1, method: "labeled_fields_v1:native", source_text: "Loan Amount: 400000" },
      },
    });
    vi.mocked(api.confirmIntake).mockResolvedValue({} as never);
    vi.mocked(api.startIntakeRun).mockResolvedValue({ run_id: "run-pdf-1" });
    const user = userEvent.setup();
    render(<DocumentIntake />);

    await user.upload(screen.getByLabelText(/Select PDF documents/), new File(["%PDF-1.7"], "application.pdf", { type: "application/pdf" }));
    fireEvent.submit(screen.getByRole("button", { name: "Process documents" }).closest("form")!);
    expect(await screen.findByDisplayValue("Alex Morgan")).toBeVisible();
    expect(screen.getAllByText(/application.pdf, page 1/).length).toBeGreaterThan(0);
    expect(screen.getByLabelText(/SSN is present/)).toBeChecked();
    await user.type(screen.getByLabelText("Reviewed and confirmed by"), "Case Reviewer");
    await user.click(screen.getByRole("button", { name: "Confirm facts and start case" }));

    await waitFor(() => expect(api.confirmIntake).toHaveBeenCalled());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/loans/runs/run-pdf-1"));
  });

  it("rejects a non-finite numeric field before confirmation", async () => {
    const user = userEvent.setup();
    vi.mocked(api.uploadIntake).mockResolvedValue({
      intake_id: "draft-1", status: "needs_review", revision: 1, ocr_available: true, warnings: [], documents: [],
      proposed_fields: {
        "borrower.name": { value: "Alex Morgan", filename: "application.pdf", page: 1, method: "native", source_text: "" },
        "property.address": { value: "1420 Alder St", filename: "application.pdf", page: 1, method: "native", source_text: "" },
        "property.estimated_value": { value: 500000, filename: "application.pdf", page: 1, method: "native", source_text: "" },
        "loan.loan_amount": { value: 400000, filename: "application.pdf", page: 1, method: "native", source_text: "" },
      },
    });
    render(<DocumentIntake />);
    await user.upload(screen.getByLabelText(/Select PDF documents/), new File(["%PDF-1.7"], "application.pdf", { type: "application/pdf" }));
    fireEvent.submit(screen.getByRole("button", { name: "Process documents" }).closest("form")!);
    await screen.findByDisplayValue("Alex Morgan");
    await user.type(screen.getByLabelText("Annual base income"), "Infinity");
    await user.type(screen.getByLabelText("Reviewed and confirmed by"), "Case Reviewer");
    await user.click(screen.getByRole("button", { name: "Confirm facts and start case" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Annual base income must be a finite number");
    expect(api.confirmIntake).not.toHaveBeenCalled();
  });
});
