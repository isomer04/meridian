import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { ArchitectureDocuments } from "./ArchitectureDocuments";

vi.mock("@/lib/api/client", () => ({
  api: { document: vi.fn() },
}));
import { api } from "@/lib/api/client";

describe("ArchitectureDocuments", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the hierarchy diagram distinguishing the underwriter from the orchestrator", () => {
    vi.mocked(api.document).mockImplementation(
      (slug) =>
        new Promise(() => {
          void slug;
        }),
    );
    render(<ArchitectureDocuments />);
    expect(screen.getByText("OriginationFlow")).toBeInTheDocument();
    expect(screen.getByText(/three peer llm agents/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        /Underwriter Agent makes the core loan judgment, but it does not lead/,
      ),
    ).toBeInTheDocument();
  });

  it("renders both allowlisted documents as Markdown once loaded", async () => {
    vi.mocked(api.document).mockImplementation(async (slug) => ({
      slug,
      title: slug === "architecture" ? "Architecture" : "Assumptions",
      markdown:
        slug === "architecture"
          ? "# Architecture\n\n## The thesis\n\nDeterministic control, bounded judgment."
          : "# Assumptions\n\n## What is measured vs. modeled\n\nMeasured vs modeled distinction.",
    }));

    render(<ArchitectureDocuments />);

    expect(await screen.findByText("The thesis")).toBeInTheDocument();
    expect(
      await screen.findByText("What is measured vs. modeled"),
    ).toBeInTheDocument();
  });

  it("renders successful documents when another document fails", async () => {
    vi.mocked(api.document).mockImplementation(async (slug) => {
      if (slug === "assumptions") throw new Error("Assumptions unavailable");
      return { slug, title: "Architecture", markdown: "# Loaded architecture" };
    });

    render(<ArchitectureDocuments />);

    expect(await screen.findByText("Loaded architecture")).toBeInTheDocument();
    expect(await screen.findByText(/Assumptions unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/Loading assumptions/)).not.toBeInTheDocument();
  });
});
