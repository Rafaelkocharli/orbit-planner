// Typed client for the backend API. Every request carries X-Client-Id so that runs
// of different browsers are isolated; the id is generated once and kept locally.

export type Goal = "priority" | "revenue";
export type Dict = Record<string, unknown>;

export interface Summary {
  steps_executed: number;
  jobs_total: number;
  jobs_completed: number;
  jobs_due: number;
  jobs_due_missed: number;
  critical_jobs_due: number;
  critical_jobs_completed_on_time: number;
  revenue_usd: number;
  blocked_command_count: number;
  below_reserve_satellite_steps: number;
  brownout_satellite_steps: number;
  critical_soc_satellite_steps: number;
  minimum_soc_pct: number;
  terminal_soc_pct: Record<string, number>;
  work_steps_in_missed_jobs: number;
}

export interface SatState {
  energy_wh: number;
  soc_pct: number;
  temp_c: number;
  calibration_age_steps: number;
  calibration_left_steps: number;
  available: boolean;
}

export interface GameEvent {
  id: string;
  at_step: number;
  type: string;
  [k: string]: unknown;
}

export interface RunState {
  run_id: string;
  label: string;
  scenario_id: string;
  scenario_title: string;
  goal: Goal;
  goal_history: { step: number; goal: Goal }[];
  planner: string;
  params: Dict;
  step: number;
  steps: number;
  step_s: number;
  finished: boolean;
  summary: Summary;
  satellites: Record<string, SatState>;
  events: GameEvent[];
  parent: Dict | null;
  import_check?: Dict;
}

export interface ScenarioInfo {
  id: string;
  title: string;
  source: "builtin" | "custom";
  steps: number;
  satellites: number;
  jobs: number;
  downlink_parallel_limit: number;
}

export interface Meta {
  goals: Goal[];
  planners: Record<string, string>;
  event_types: string[];
  override_keys: string[];
  scenarios: ScenarioInfo[];
}

export interface RunListItem {
  id: string;
  label: string;
  scenario_id: string;
  scenario_title: string;
  planner: string;
  goal: Goal;
  steps_executed: number;
  steps: number;
  created_at: number;
  parent: Dict | null;
  brief: Dict;
}

export interface Task {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  message: string;
  result: unknown;
  error: string | null;
}

export interface Job {
  id: string;
  kind: "downlink" | "relay";
  priority: number;
  value_usd: number;
  release_step: number;
  deadline_step: number;
  work_steps: number;
  remaining_steps: number;
  eligible_satellites: string[];
  status: string;
  work_done: number;
  executors: string[];
  completed_step?: number;
  usable_steps_left?: number;
}

export interface Brief {
  jobs_completed: number;
  jobs_due_missed: number;
  critical_jobs_due: number;
  critical_jobs_completed_on_time: number;
  critical_rate: number | null;
  revenue_usd: number;
  blocked_command_count: number;
  below_reserve_satellite_steps: number;
  brownout_satellite_steps: number;
  minimum_soc_pct: number;
  mean_terminal_soc_pct: number;
  work_steps_in_missed_jobs: number;
  [k: string]: number | null;
}

export interface Verdict {
  goal: Goal;
  preferred: string | null;
  comparable: boolean;
  reason: string;
  trade_offs: string[];
}

function clientId(): string {
  const key = "orbit-client-id";
  try {
    let id = localStorage.getItem(key);
    if (!id) {
      id = "c" + crypto.randomUUID().replace(/-/g, "").slice(0, 24);
      localStorage.setItem(key, id);
    }
    return id;
  } catch {
    return "public";
  }
}

const CLIENT = clientId();
const headers = (json: boolean) => ({ "X-Client-Id": CLIENT, ...(json ? { "Content-Type": "application/json" } : {}) });

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method,
    headers: headers(body !== undefined),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) {
    const detail = (data as { detail?: unknown })?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((d: { msg?: string; loc?: unknown[] }) => `${(d.loc ?? []).join(".")}: ${d.msg}`).join("; ")
      : r.statusText;
    throw new Error(msg || `HTTP ${r.status}`);
  }
  return data as T;
}

const get = <T>(p: string) => call<T>("GET", p);
const post = <T>(p: string, b: unknown = {}) => call<T>("POST", p, b);
const qs = (o: Record<string, string | number | undefined>) =>
  "?" + Object.entries(o).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

/** POST a background operation and poll until it finishes. */
async function runTask<T>(path: string, body: Dict, onProgress?: (t: Task) => void): Promise<T> {
  let t = await post<Task>(path, { ...body, background: true });
  while (t.status === "queued" || t.status === "running") {
    onProgress?.(t);
    await new Promise((r) => setTimeout(r, 250));
    t = await get<Task>(`/tasks/${t.id}`);
  }
  onProgress?.(t);
  if (t.status === "failed") throw new Error(t.error ?? "Задача завершилась с ошибкой");
  return t.result as T;
}

async function download(path: string, filename: string) {
  const r = await fetch(`/api${path}`, { headers: headers(false) });
  if (!r.ok) throw new Error(`Не удалось скачать: HTTP ${r.status}`);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function openHtml(path: string) {
  const w = window.open("", "_blank");
  const r = await fetch(`/api${path}`, { headers: headers(false) });
  const html = await r.text();
  if (w) {
    w.document.open();
    w.document.write(html);
    w.document.close();
  }
}

export const api = {
  meta: () => get<Meta>("/meta"),
  scenario: (id: string) => get<Dict>(`/scenarios/${id}`),
  saveScenario: (body: Dict) => post<ScenarioInfo>("/scenarios", body),

  runs: () => get<RunListItem[]>("/runs"),
  create: (body: Dict) => post<RunState>("/runs", body),
  importResult: (record: unknown) => post<RunState>("/runs/import", record),
  run: (id: string) => get<RunState>(`/runs/${id}`),
  remove: (id: string) => call<Dict>("DELETE", `/runs/${id}`),
  advance: (id: string, body: { steps?: number; until_step?: number }, onProgress?: (t: Task) => void) =>
    runTask<RunState>(`/runs/${id}/advance`, body, onProgress),
  event: (id: string, event: Dict) => post<RunState>(`/runs/${id}/events`, event),
  goal: (id: string, goal: Goal) => post<RunState>(`/runs/${id}/goal`, { goal }),
  fork: (id: string, body: Dict) => post<RunState>(`/runs/${id}/fork`, body),

  jobs: (id: string, f: Record<string, string | number | undefined> = {}) => get<Job[]>(`/runs/${id}/jobs${qs(f)}`),
  schedule: (id: string) => get<{ lanes: Record<string, ScheduleItem[]> }>(`/runs/${id}/schedule`),
  series: (id: string, sid: string) => get<Series>(`/runs/${id}/satellites/${sid}/series`),
  analytics: (id: string) => get<Analytics>(`/runs/${id}/analytics`),
  why: (id: string, jobId: string) => get<Dict>(`/runs/${id}/jobs/${encodeURIComponent(jobId)}/why`),
  explain: (id: string, step: number, sid: string) => get<Explain>(`/runs/${id}/explain${qs({ step, satellite_id: sid })}`),
  planner: (id: string) => get<PlannerReport>(`/runs/${id}/planner`),
  forecast: (id: string, onProgress?: (t: Task) => void) => runTask<Forecast>(`/runs/${id}/forecast`, {}, onProgress),
  compare: (a: string, b: string, goal?: Goal) => post<Comparison>("/compare", { a, b, goal }),
  strategies: (id: string, body: Dict, onProgress?: (t: Task) => void) =>
    runTask<StrategyResult>(`/runs/${id}/strategies`, body, onProgress),
  whatIfEvent: (id: string, event: Dict, onProgress?: (t: Task) => void) =>
    runTask<WhatIfEventResult>(`/runs/${id}/what-if/event`, { event }, onProgress),
  whatIfResources: (id: string, variations: Dict[], onProgress?: (t: Task) => void) =>
    runTask<WhatIfResourcesResult>(`/runs/${id}/what-if/resources`, { variations }, onProgress),

  downloadResult: (id: string) => download(`/runs/${id}/result`, `result-${id}.json`),
  openReport: (id: string) => openHtml(`/runs/${id}/report`),
};

export interface ScheduleItem {
  start_step: number;
  end_step: number;
  action: "job" | "calibrate" | "idle";
  label: string;
  blocked: boolean;
  reason: string | null;
  kind?: string;
  priority?: number;
}

export interface Series {
  satellite_id: string;
  capacity_wh: number;
  limits: { reserve_soc_pct: number; critical_soc_pct: number; payload_min_c: number; payload_max_c: number; calibration_valid_steps: number };
  step: number[];
  soc_pct: number[];
  temp_c: number[];
  calibration_age_steps: number[];
  action: string[];
  job_id: (string | null)[];
  environment: { solar_w: number[]; downlink_available: boolean[]; relay_available: boolean[]; thermal_target_c: number[] };
}

export interface MissCause {
  cause: string;
  text: string;
  jobs: number;
  value_usd: number;
  avoidable: boolean;
}

export interface MissedJob {
  id: string;
  kind: string;
  priority: number;
  value_usd: number;
  window: [number, number];
  work_done: number;
  work_steps: number;
  primary: string;
  text: string;
  breakdown: Record<string, number>;
  proof?: { statement: string; usable_steps: number[] };
  competing_jobs?: { id: string; priority: number; value_usd: number; steps: number }[];
}

export interface Analytics {
  step: number;
  summary: Summary;
  critical_rate: number | null;
  by_priority: { priority: number; total: number; due: number; completed: number; completed_on_time_of_due: number; rate: number | null; revenue_usd: number; value_lost_usd: number }[];
  utilization: { fleet_utilization: number | null; satellites: Record<string, { job_steps: number; calibrate_steps: number; idle_steps: number; blocked_commands: number; unavailable_steps: number; utilization: number | null }> };
  deficit_periods: { satellite_id: string; start_step: number; end_step: number; min_soc_pct: number; critical: boolean; brownout: boolean }[];
  blocked_commands: { count: number; by_reason: Record<string, number>; items: { step: number; satellite_id: string; requested: Dict; reason: string }[] };
  missed: { count: number; by_cause: MissCause[]; jobs: MissedJob[] };
}

export interface Explain {
  step: number;
  satellite_id: string;
  known: { events_received: { id: string; type: string; at_step: number }[]; jobs_known: number; open_jobs_for_satellite: number };
  state_before: Record<string, unknown>;
  decision: { requested: Dict; executed: string; reason: string; completed_job: string | null };
  consequences: Record<string, unknown>;
  options: { action: string; job_id?: string; kind?: string; priority?: number; value_usd?: number; deadline_step?: number; remaining_steps?: number; admissible: boolean; reason: string }[];
}

export interface Forecast {
  note: string;
  as_of_step: number;
  current: Brief;
  projected: Brief;
  delta: Brief;
  warnings: { type: string; step: number; in_steps: number; message: string; satellite_id?: string; job_id?: string }[];
}

export interface Comparison {
  a: { run_id: string; label: string; goal: Goal; planner: string; step: number };
  b: { run_id: string; label: string; goal: Goal; planner: string; step: number };
  same_conditions: boolean;
  origin: Dict | null;
  notes: string[];
  metrics: { a: Brief; b: Brief; delta: Brief };
  only_a_completed: JobListing;
  only_b_completed: JobListing;
  verdict: Verdict;
}

export interface JobListing {
  count: number;
  value_usd: number;
  critical: number;
  items: { id: string; kind: string; priority: number; value_usd: number }[];
}

export interface StrategyResult {
  note: string;
  from_step: number;
  rank_goal: Goal;
  ranking: { run_id: string; label: string; planner: string; goal: Goal; metrics: Brief }[];
  best: string;
  verdict_vs_runner_up: Verdict | null;
}

export interface WhatIfEventResult {
  note: string;
  without: Brief;
  with: Brief;
  delta: Brief;
  new_jobs: { total: number; completed: { id: string }[]; not_completed: { id: string }[] };
  displaced_jobs: { id: string; priority: number; value_usd: number; kind: string }[];
  verdict: Verdict;
  summary: string;
}

export interface WhatIfResourcesResult {
  note: string;
  results: { label: string; overrides: Dict; metrics: Brief; delta?: Brief; verdict?: Verdict; event_errors: Dict[] }[];
}

export interface PlannerReport {
  planner: string;
  version: string;
  params: Dict;
  report: null | {
    replans: number;
    proven_optimal: number;
    fallbacks: number;
    by_reason: Record<string, number>;
    solves: { step: number; reason: string; status: string; wall_s: number; horizon: number; variables: number; jobs_considered: number; planned_completions?: number; gap?: number }[];
  };
}
