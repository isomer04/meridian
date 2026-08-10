// Friendly aliases for the contract generated from FastAPI's OpenAPI schema.
// Keep API shapes in `openapi.ts`; add only client-side adaptations here.
import type { components } from "./openapi";

type Schema = components["schemas"];

export type Decision = Schema["Decision"];
export type RunStatus = Schema["RunResponse"]["status"];
export type Scenario = Schema["ScenarioOption"];
export type SystemState = Schema["SystemResponse"];
export type RunRequest = Schema["StartRunRequest"];

export type Condition = Schema["ConditionView"];
export type Citation = Schema["CitationView"];
export type Finding = Schema["FindingView"];
export type LedgerEntry = Schema["LedgerEntryView"];
export type OverlayConflict = Schema["OverlayConflictView"];
export type ValueAcceptance = Schema["ValueAcceptanceView"];
export type GuidelineFinding = Schema["GuidelineFindingView"];
export type Compensation = Schema["CompensationView"];
export type Notice = Schema["NoticeView"];
export type CycleTime = Schema["CycleTimeView"];
export type AgentRun = Schema["AgentRunView"];
export type Idempotency = Schema["IdempotencyView"];
export type LoanResult = Schema["LoanRunResult"];

export type Approval = Schema["ApprovalItem"];
export type ApprovalQueueResponse = Schema["ApprovalQueueResponse"];
export type ApprovalDecisionBody = Schema["ApprovalDecisionBody"];

export type EventRecord = Schema["EventEnvelope"] & {
  kind?: string;
  payload: NonNullable<Schema["EventEnvelope"]["payload"]>;
};

export type RunUpdate = Omit<
  Schema["RunResponse"],
  "events" | "trace_lines" | "result" | "approval"
> & {
  events: EventRecord[];
  trace_lines: string[];
  result?: LoanResult | null;
  approval?: Approval | null;
};

export type EvaluationReport = Schema["EvaluationReportResponse"];
export type EvaluationStatus = Schema["EvaluationStatusResponse"]["status"];
export type StartEvaluationResponse = Schema["StartEvaluationResponse"];
export type EvaluationStatusResponse = Schema["EvaluationStatusResponse"];
export type DocumentResponse = Schema["DocumentResponse"];
export type DocumentSlug = DocumentResponse["slug"];
