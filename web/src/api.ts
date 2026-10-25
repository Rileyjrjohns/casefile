export type Recommendation = "close" | "escalate" | "request_information";
export type ReviewAction = "validate" | "correct" | "reject";
export interface Case {
  id: string;
  alert_id: string;
  subject_id: string;
  kind: string;
  description: string;
  opened_at: string;
}
export interface Source {
  tool: string;
  record_id: string;
}
export interface Conclusion {
  id: string;
  statement: string;
  confidence: { value: number };
  sources: Source[];
}
export interface Run {
  id: string;
  case_id: string;
  state:
    | "PENDING"
    | "GATHERING"
    | "ANALYZING"
    | "AWAITING_REVIEW"
    | "CLOSED"
    | "FAILED";
  failure_reason: string | null;
  limits?: { tool_calls: number };
  usage: { tool_calls: number; tokens: number; estimated_cost: string };
  analysis: {
    summary: string;
    recommendation: Recommendation;
    conclusions: Conclusion[];
  } | null;
  human_decision: { recommendation: Recommendation; decided_at: string } | null;
}
export interface RunDetail {
  run: Run;
  revision: number;
  events: unknown[];
  review?: { conclusions: ConclusionInput[] } | null;
  references?: Record<string, string>;
}
export interface ConclusionInput {
  conclusion_id: string;
  action: ReviewAction;
  corrected_statement?: string;
  reason?: string;
}
export interface ReviewInput {
  expected_revision: number;
  confirmed: boolean;
  decision: Recommendation;
  conclusions: ConclusionInput[];
}
export interface CasefileApi {
  cases(signal?: AbortSignal): Promise<Case[]>;
  runs(caseId: string, signal?: AbortSignal): Promise<Run[]>;
  run(runId: string, signal?: AbortSignal): Promise<RunDetail>;
  start(caseId: string): Promise<Run>;
  source(runId: string, source: Source): Promise<unknown>;
  review(runId: string, review: ReviewInput): Promise<unknown>;
  promote(runId: string, conclusionId: string): Promise<{ id: string }>;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const message =
      body &&
      typeof body === "object" &&
      "message" in body &&
      typeof body.message === "string"
        ? body.message
        : `Request failed (${response.status}). Please reload and try again.`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api: CasefileApi = {
  cases: async (signal) =>
    (await request<{ cases: Case[] }>("/cases", { signal })).cases,
  runs: async (id, signal) =>
    (await request<{ runs: Run[] }>(`/cases/${id}/runs`, { signal })).runs,
  run: (id, signal) => request<RunDetail>(`/runs/${id}`, { signal }),
  start: (id) =>
    request<Run>(`/cases/${id}/runs`, { method: "POST", body: "{}" }),
  source: (id, source) =>
    request(
      `/runs/${id}/sources/${encodeURIComponent(source.tool)}/${encodeURIComponent(source.record_id)}`,
    ),
  review: (id, input) =>
    request(`/runs/${id}/review`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  promote: (runId, conclusionId) =>
    request(`/runs/${runId}/conclusions/${conclusionId}/reference`, {
      method: "POST",
      body: JSON.stringify({ confirmed: true }),
    }),
};
