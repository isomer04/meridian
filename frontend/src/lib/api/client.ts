import type {
  ApprovalDecisionBody,
  ApprovalQueueResponse,
  DocumentResponse,
  DocumentSlug,
  EvaluationReport,
  EvaluationStatusResponse,
  RunRequest,
  RunUpdate,
  Scenario,
  StartEvaluationResponse,
  SystemState,
} from "./generated";
import type { components } from "./openapi";

type ApiRunResponse = components["schemas"]["RunResponse"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}
export type IntakeDraft = {
  intake_id: string;
  status: "needs_review" | "confirmed";
  revision: number;
  ocr_available: boolean;
  warnings: string[];
  documents: Array<{ document_id: string; filename: string; sha256: string; page_count: number; pages: Array<{ page: number; method: string }> }>;
  proposed_fields: Record<string, { value: string | number | boolean; filename: string; page: number; method: string; source_text: string }>;
};
const base = () => process.env.NEXT_PUBLIC_MERIDIAN_API_ORIGIN ?? "";
const REQUEST_TIMEOUT_MS = 15_000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    const timeoutSignal = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
    const signal = init?.signal
      ? AbortSignal.any([init.signal, timeoutSignal])
      : timeoutSignal;
    response = await fetch(`${base()}/api/v1${path}`, {
      ...init,
      signal,
      headers: { "content-type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      `Python API unavailable at ${base() || "this origin"}.`,
    );
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? data.detail
        : data;
    let message = `Request failed (${response.status})`;
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object" && "message" in detail) {
      if (typeof detail.message === "string") message = detail.message;
    }
    throw new ApiError(response.status, message, detail);
  }
  return data as T;
}
export const api = {
  system: () => request<SystemState>("/system"),
  scenarios: () => request<Scenario[]>("/scenarios"),
  startRun: (body: RunRequest) =>
    request<{ run_id: string }>("/runs", {
      method: "POST",
      body: JSON.stringify(body),
  }),
  run: async (id: string): Promise<RunUpdate> => {
    const run = await request<ApiRunResponse>(
      `/runs/${encodeURIComponent(id)}`,
    );
    return {
      ...run,
      events: (run.events ?? []).map((event) => ({
        ...event,
        kind: event.type,
        payload: event.payload ?? {},
      })),
      trace_lines: run.trace_lines ?? [],
      approval: run.approval as RunUpdate["approval"],
    };
  },
  approvals: async (): Promise<import("./generated").Approval[]> =>
    (await request<ApprovalQueueResponse>("/approvals")).approvals,
  decideApproval: (loanId: string, gate: string, body: ApprovalDecisionBody) =>
    request<void>(
      `/approvals/${encodeURIComponent(loanId)}/${encodeURIComponent(gate)}/decision`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  evaluationReport: () => request<EvaluationReport>("/evaluation/report"),
  startEvaluation: () =>
    request<StartEvaluationResponse>("/evaluations", { method: "POST" }),
  evaluation: (id: string) =>
    request<EvaluationStatusResponse>(`/evaluations/${encodeURIComponent(id)}`),
  document: (slug: DocumentSlug) =>
    request<DocumentResponse>(`/documents/${encodeURIComponent(slug)}`),
  uploadIntake: async (files: File[]): Promise<IntakeDraft> => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    let response: Response;
    try {
      response = await fetch(`${base()}/api/v1/intakes`, {
        method: "POST", body, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
    } catch (reason) {
      if (reason && typeof reason === "object" && "name" in reason && reason.name === "TimeoutError") {
        throw new ApiError(0, `Request timed out after ${REQUEST_TIMEOUT_MS / 1000} seconds.`);
      }
      throw new ApiError(0, `Python API unavailable at ${base() || "this origin"}.`);
    }
    const data = await response.json().catch(() => undefined);
    if (!response.ok) {
      const detail = data?.detail;
      throw new ApiError(response.status, detail?.message ?? `Upload failed (${response.status})`, detail);
    }
    return data as IntakeDraft;
  },
  confirmIntake: (id: string, revision: number, reviewer: string, caseData: Record<string, unknown>) =>
    request<IntakeDraft>(`/intakes/${encodeURIComponent(id)}/confirm`, {
      method: "POST", body: JSON.stringify({ expected_revision: revision, reviewer, case: caseData }),
    }),
  startIntakeRun: (id: string) =>
    request<{ run_id: string }>(`/intakes/${encodeURIComponent(id)}/runs`, {
      method: "POST", body: JSON.stringify({ judgment_kind: "stub", roster: "production", auto_approve: false }),
    }),
  decisionPackageUrl: (runId: string) => `${base()}/api/v1/runs/${encodeURIComponent(runId)}/decision-package.pdf`,
};
