import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { Shell } from "./Shell";
import { api, ApiError } from "@/lib/api/client";

const route = vi.hoisted(() => ({ path: "/loans/new" }));

vi.mock("next/navigation", () => ({ usePathname: () => route.path }));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: { system: vi.fn() },
}));

describe("Shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    route.path = "/loans/new";
  });
  afterEach(() => cleanup());

  it("marks the current route in primary navigation and reports API availability", async () => {
    vi.mocked(api.system).mockResolvedValue({
      version: "0.1.0",
      api_key_present: true,
      default_judgment: "stub",
      crew_available: true,
      active_run_id: "run-123",
    });
    render(
      <Shell>
        <h1>New underwriting case</h1>
      </Shell>,
    );
    const [nav] = screen.getAllByRole("navigation", {
      name: "Primary navigation",
    });
    expect(nav.querySelector('a[href="/loans/new"]')).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(nav.querySelector('a[href="/approvals"]')).not.toHaveAttribute(
      "aria-current",
    );
    expect(await screen.findAllByText("API connected")).not.toHaveLength(0);
    expect(screen.getAllByRole("link", { name: /View active run/i })[0]).toHaveAttribute(
      "href",
      "/loans/runs/run-123",
    );
    expect(screen.getByRole("main")).toHaveClass("main-standard");
  });

  it("shows a retry action when the Python API is unavailable", async () => {
    vi.mocked(api.system).mockRejectedValue(
      new ApiError(0, "Python API unavailable."),
    );
    render(
      <Shell>
        <h1>New underwriting case</h1>
      </Shell>,
    );
    expect(
      await screen.findAllByText("Python API unavailable"),
    ).not.toHaveLength(0);
    const [retry] = screen.getAllByRole("button", { name: "Retry connection" });
    vi.mocked(api.system).mockResolvedValue({
      version: "0.1.0",
      api_key_present: false,
      default_judgment: "stub",
      crew_available: false,
    });
    await userEvent.click(retry);
    await waitFor(() =>
      expect(screen.getAllByText("API connected").length).toBeGreaterThan(0),
    );
  });

  it("exposes a skip link and a mobile navigation disclosure", () => {
    render(
      <Shell>
        <h1>New underwriting case</h1>
      </Shell>,
    );
    expect(
      screen.getAllByRole("link", { name: "Skip to main content" })[0],
    ).toHaveAttribute("href", "#main");
    const mobileNav = screen.getAllByRole("navigation", {
      name: "Primary navigation",
    });
    expect(mobileNav.length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("Navigation")[0].closest("summary"),
    ).toBeInTheDocument();
  });

  it("makes the mobile navigation disclosure keyboard-focusable", async () => {
    vi.mocked(api.system).mockResolvedValue({
      version: "0.1.0",
      api_key_present: false,
      default_judgment: "stub",
      crew_available: false,
    });
    render(
      <Shell>
        <h1>New underwriting case</h1>
      </Shell>,
    );
    const [summary] = screen.getAllByText("Navigation");
    const details = summary.closest("details") as HTMLDetailsElement;
    // <summary> keyboard activation (Enter/Space toggling `open`) is native browser
    // behavior that jsdom does not implement; this test asserts the disclosure is
    // reachable and activatable, and trusts browser-native semantics for the toggle
    // itself (exercised by the Playwright mobile-nav coverage against a real engine).
    summary.focus();
    expect(summary).toHaveFocus();
    await userEvent.click(summary);
    expect(details.open).toBe(true);
  });

  it("reaches the skip link as the first Tab stop", async () => {
    render(
      <Shell>
        <h1>New underwriting case</h1>
      </Shell>,
    );
    await userEvent.tab();
    expect(
      screen.getAllByRole("link", { name: "Skip to main content" })[0],
    ).toHaveFocus();
  });

  it("moves focus to the main content after a route change", async () => {
    const { rerender } = render(
      <Shell>
        <h1>New underwriting case</h1>
      </Shell>,
    );
    const main = screen.getByRole("main");
    expect(main).toHaveAttribute("tabindex", "-1");
    expect(main).not.toHaveFocus();

    screen.getAllByRole("link", { name: "Evaluation" })[0].focus();
    route.path = "/evaluation";
    rerender(
      <Shell>
        <h1>Evaluation evidence</h1>
      </Shell>,
    );

    await waitFor(() => expect(main).toHaveFocus());
  });
});
