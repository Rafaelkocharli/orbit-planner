import { ReactNode, useState } from "react";
import type { Brief } from "../api";

export const GOAL_TEXT: Record<string, string> = {
  priority: "Приоритетное обслуживание",
  revenue: "Коммерческая отдача",
};

export const CAUSE_TEXT: Record<string, string> = {
  infeasible_contacts: "Невыполнимо по контактам",
  busy_other_job: "Аппараты заняты другими заданиями",
  ground_capacity: "Каналы связи с Землёй заняты",
  calibration_expired: "Калибровка истекла",
  calibrating: "Калибровка в шаг связи",
  energy_reserve: "Резерв заряда",
  thermal_limit: "Температура",
  satellite_unavailable: "Аппарат недоступен",
  not_scheduled: "Не запланировано алгоритмом",
  blocked: "Команда отклонена",
  received_too_late: "Поступило слишком поздно",
};

export const REASON_TEXT: Record<string, string> = {
  accepted: "допустимо",
  idle: "ожидание",
  satellite_unavailable: "аппарат недоступен",
  calibration_required: "нужна калибровка",
  energy_reserve: "резерв заряда",
  thermal_limit: "температура вне диапазона",
  no_contact: "нет контакта",
  outside_job_window: "вне окна задания",
  ineligible_satellite: "недопустимый исполнитель",
  already_completed: "уже выполнено",
  unknown_job: "неизвестное задание",
  ground_capacity: "каналы с Землёй заняты",
  duplicate_job_in_step: "задание уже выполняет другой аппарат",
};

export const fmt = {
  n: (x: number | null | undefined, d = 0) =>
    x === null || x === undefined ? "—" : x.toLocaleString("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: d }),
  usd: (x: number | null | undefined) => (x === null || x === undefined ? "—" : `$${fmt.n(x, 2)}`),
  pct: (x: number | null | undefined, d = 1) => (x === null || x === undefined ? "—" : `${fmt.n(100 * x, d)}%`),
  signed: (x: number | null | undefined, d = 0) =>
    x === null || x === undefined ? "—" : `${x > 0 ? "+" : ""}${fmt.n(x, d)}`,
  time: (step: number, stepS = 300) => {
    const t = step * stepS;
    return `${String(Math.floor(t / 3600)).padStart(2, "0")}:${String(Math.floor((t % 3600) / 60)).padStart(2, "0")}`;
  },
};

export function Table({ head, rows, empty = "Нет данных", dense }: {
  head: ReactNode[];
  rows: ReactNode[][];
  empty?: string;
  dense?: boolean;
}) {
  if (!rows.length) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table className={dense ? "dense" : undefined}>
        <thead>
          <tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (t: T) => void;
}) {
  return (
    <nav className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={value === t.id ? "active" : ""}
          onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </nav>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" }) {
  return (
    <div className={`stat ${tone ?? ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub !== undefined && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function Card({ title, children, actions }: { title?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function VerdictBox({ v }: { v: { preferred: string | null; comparable: boolean; reason: string; trade_offs: string[] } | null | undefined }) {
  if (!v) return null;
  return (
    <div className={`verdict ${v.comparable ? "neutral" : "good"}`}>
      <strong>{v.comparable ? "Результаты сопоставимы" : `Предпочтительнее: ${v.preferred}`}</strong>
      <div>{v.reason}</div>
      {v.trade_offs.length > 0 && <div className="muted">Компромиссы: {v.trade_offs.join("; ")}</div>}
    </div>
  );
}

const BRIEF_ROWS: [keyof Brief, string, (x: number | null) => string, boolean][] = [
  ["critical_jobs_completed_on_time", "Приоритет 3 в срок", (x) => fmt.n(x), true],
  ["critical_rate", "Доля приоритета 3", (x) => fmt.pct(x), true],
  ["jobs_completed", "Выполнено заданий", (x) => fmt.n(x), true],
  ["jobs_due_missed", "Просрочено", (x) => fmt.n(x), false],
  ["revenue_usd", "Выручка", (x) => fmt.usd(x), true],
  ["mean_terminal_soc_pct", "Средний конечный заряд, %", (x) => fmt.n(x, 1), true],
  ["minimum_soc_pct", "Минимальный заряд, %", (x) => fmt.n(x, 1), true],
  ["below_reserve_satellite_steps", "Шагов ниже резерва", (x) => fmt.n(x), false],
  ["blocked_command_count", "Отклонённых команд", (x) => fmt.n(x), false],
  ["work_steps_in_missed_jobs", "Работа в просроченных, шагов", (x) => fmt.n(x), false],
];

/** Side-by-side metrics with a colored delta (green = better for the operator). */
export function BriefCompare({ cols }: { cols: { name: string; brief: Brief }[] }) {
  const base = cols[0].brief;
  return (
    <Table
      head={["Показатель", ...cols.map((c) => c.name)]}
      rows={BRIEF_ROWS.map(([k, label, f, higherBetter]) => [
        label,
        ...cols.map((c, i) => {
          const v = c.brief[k] as number | null;
          if (i === 0 || v === null || base[k] === null) return f(v);
          const d = v - (base[k] as number);
          const tone = Math.abs(d) < 1e-9 ? "" : (d > 0) === higherBetter ? "good" : "bad";
          return (
            <span key={i}>
              {f(v)} <span className={`delta ${tone}`}>{k === "critical_rate" ? fmt.signed(100 * d, 1) + " п.п." : fmt.signed(d, k === "revenue_usd" || String(k).includes("soc") ? 2 : 0)}</span>
            </span>
          );
        }),
      ])}
    />
  );
}

export interface LineSpec {
  values: number[];
  color: string;
  label: string;
}

/** Minimal responsive SVG line chart with optional horizontal limits and step marker. */
export function LineChart({ x, lines, limits = [], height = 180, yLabel, marker }: {
  x: number[];
  lines: LineSpec[];
  limits?: { y: number; label: string; color: string }[];
  height?: number;
  yLabel?: string;
  marker?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 800, H = height, L = 44, R = 12, T = 12, B = 24;
  const all = [...lines.flatMap((l) => l.values), ...limits.map((l) => l.y)].filter(Number.isFinite);
  if (!x.length || !all.length) return <p className="muted">Нет данных — выполните шаги расчёта.</p>;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const x0 = x[0], x1 = Math.max(x[x.length - 1], x0 + 1);
  const sx = (v: number) => L + ((v - x0) / (x1 - x0)) * (W - L - R);
  const sy = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const ticks = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * i) / 4);
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const v = x0 + ((px - L) / (W - L - R)) * (x1 - x0);
    let best = 0;
    x.forEach((xv, i) => { if (Math.abs(xv - v) < Math.abs(x[best] - v)) best = i; });
    setHover(best);
  };
  return (
    <div className="chart">
      <div className="legend">
        {lines.map((l) => <span key={l.label}><i style={{ background: l.color }} />{l.label}</span>)}
        {limits.map((l) => <span key={l.label}><i className="dash" style={{ borderColor: l.color }} />{l.label}</span>)}
        {hover !== null && (
          <span className="muted">шаг {x[hover]}: {lines.map((l) => `${l.label} ${fmt.n(l.values[hover], 1)}`).join(" · ")}</span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
        role="img" aria-label={yLabel}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={L - 6} y={sy(t) + 4} textAnchor="end" className="tick">{fmt.n(t, Math.abs(hi - lo) < 10 ? 1 : 0)}</text>
          </g>
        ))}
        {limits.map((l) => (
          <line key={l.label} x1={L} x2={W - R} y1={sy(l.y)} y2={sy(l.y)} stroke={l.color} strokeDasharray="5 4" strokeWidth={1.5} />
        ))}
        {lines.map((l) => (
          <polyline key={l.label} fill="none" stroke={l.color} strokeWidth={2} vectorEffect="non-scaling-stroke"
            points={l.values.map((v, i) => `${sx(x[i])},${sy(v)}`).join(" ")} />
        ))}
        {marker !== undefined && <line x1={sx(marker)} x2={sx(marker)} y1={T} y2={H - B} className="marker" />}
        {hover !== null && <line x1={sx(x[hover])} x2={sx(x[hover])} y1={T} y2={H - B} className="hover" />}
        <text x={L} y={H - 6} className="tick">{x0}</text>
        <text x={W - R} y={H - 6} textAnchor="end" className="tick">{x1}</text>
      </svg>
    </div>
  );
}

export function Progress({ value, text }: { value: number; text?: string }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(value * 100)}>
      <div style={{ width: `${Math.round(value * 100)}%` }} />
      <span>{text ?? `${Math.round(value * 100)}%`}</span>
    </div>
  );
}
