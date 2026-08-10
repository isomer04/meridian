import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownDocument } from "./MarkdownDocument";

describe("MarkdownDocument", () => {
  it("renders GFM content with linkable headings and accessible overflow regions", () => {
    render(
      <MarkdownDocument
        markdown={[
          "# Risk Summary",
          "",
          "| Signal | Result |",
          "| --- | --- |",
          "| DTI | Pass |",
          "",
          "```text",
          "a very wide line",
          "```",
        ].join("\n")}
      />,
    );

    expect(screen.getByRole("heading", { name: "Risk Summary" })).toHaveAttribute(
      "id",
      "risk-summary",
    );
    const table = within(screen.getByRole("region", { name: "Table" }));
    expect(table.getByRole("cell", { name: "DTI" })).toBeInTheDocument();
    expect(table.getByRole("cell", { name: "Pass" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Code block" })).toHaveTextContent(
      "a very wide line",
    );
  });

  it("does not turn raw HTML from Markdown into executable elements", () => {
    const { container } = render(
      <MarkdownDocument markdown={'<script>alert("xss")</script>\n<img src="x" onerror="alert(1)">'} />,
    );

    expect(container.querySelector("script")).not.toBeInTheDocument();
    expect(container.querySelector("img")).not.toBeInTheDocument();
  });
});
