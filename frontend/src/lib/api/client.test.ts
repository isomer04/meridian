import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";

const runRequest = {
  scenario_id: 1,
  judgment_kind: "stub" as const,
  roster: "production" as const,
  policy_attack: false,
  submit_twice: false,
  auto_approve: false,
};

describe("API errors", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("preserves a structured 409 detail and uses its message", async () => {
    const detail = {
      code: "run_active",
      message: "another run is already active",
      active_run_id: "run-1",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail }), {
          status: 409,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(api.startRun(runRequest)).rejects.toMatchObject({
      status: 409,
      message: detail.message,
      detail,
    });
  });

  it("preserves FastAPI's string-detail behavior for a 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "invalid request" }), {
          status: 422,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(api.startRun(runRequest)).rejects.toMatchObject({
      status: 422,
      message: "invalid request",
      detail: "invalid request",
    });
  });

  it("applies the request timeout to multipart uploads", async () => {
    const signal = new AbortController().signal;
    vi.spyOn(AbortSignal, "timeout").mockReturnValue(signal);
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ intake_id: "draft-1" }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.uploadIntake([new File(["%PDF-1.7"], "application.pdf", { type: "application/pdf" })]);

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/intakes", expect.objectContaining({ signal }));
  });

  it("reports multipart upload timeouts separately from API unavailability", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("timed out", "TimeoutError")));

    await expect(api.uploadIntake([new File(["%PDF-1.7"], "application.pdf")])).rejects.toMatchObject({
      status: 0,
      message: "Request timed out after 15 seconds.",
    });
  });
});
