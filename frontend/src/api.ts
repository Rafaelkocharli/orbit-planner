export type Summary = Record<string, unknown>;

export interface RunState {
  run_id: string;
  scenario_id: string;
  goal: string;
  planner: string;
  step: number;
  steps: number;
  finished: boolean;
  summary: Summary;
  satellites: Record<string, { soc_pct: number; temp_c: number; calibration_age_steps: number; available: boolean }>;
  events: unknown[];
  parent: { run_id: string; step: number } | null;
}

export interface Meta {
  goals: string[];
  planners: string[];
  scenarios: { id: string; title: string; steps: number; satellites: number; jobs: number }[];
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail ?? r.statusText);
  return data as T;
}

export const api = {
  meta: () => call<Meta>("GET", "/meta"),
  create: (body: object) => call<RunState>("POST", "/runs", body),
  advance: (id: string, body: { steps?: number; until_step?: number }) =>
    call<RunState>("POST", `/runs/${id}/advance`, body),
  event: (id: string, event: unknown) => call<RunState>("POST", `/runs/${id}/events`, event),
  goal: (id: string, goal: string) => call<RunState>("POST", `/runs/${id}/goal`, { goal }),
  fork: (id: string, body: object) => call<RunState>("POST", `/runs/${id}/fork`, body),
  resultUrl: (id: string) => `/api/runs/${id}/result`,
};
