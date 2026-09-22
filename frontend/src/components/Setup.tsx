import { useEffect, useMemo, useState } from "react";
import { api, Dict, Goal, Meta, RunListItem, RunState } from "../api";
import { Card, fmt, GOAL_TEXT, Table } from "./ui";

interface Props {
  meta: Meta;
  onMeta: () => void;
  onOpen: (run: RunState) => void;
  act: (fn: () => Promise<void>) => void;
  busy: boolean;
}

interface Overrides {
  socSat: string;
  socPct: string;
  solar: string;
  jobId: string;
  jobPriority: string;
  failSat: string;
  failStart: string;
  failEnd: string;
  dlLimit: string;
}

const EMPTY: Overrides = { socSat: "", socPct: "", solar: "", jobId: "", jobPriority: "3", failSat: "", failStart: "", failEnd: "", dlLimit: "" };

function buildOverrides(o: Overrides): Dict | undefined {
  const out: Dict = {};
  if (o.socSat && o.socPct !== "") out.initial_soc = { [o.socSat]: Number(o.socPct) };
  if (o.solar !== "") out.solar_factor = Number(o.solar);
  if (o.jobId.trim()) out.job_priority = { [o.jobId.trim()]: Number(o.jobPriority) };
  if (o.failSat && o.failStart !== "" && o.failEnd !== "")
    out.failures = [{ satellite_id: o.failSat, start_step: Number(o.failStart), end_step: Number(o.failEnd) }];
  if (o.dlLimit !== "") out.downlink_parallel_limit = Number(o.dlLimit);
  return Object.keys(out).length ? out : undefined;
}

async function readJson(file: File): Promise<unknown> {
  try {
    return JSON.parse(await file.text());
  } catch {
    throw new Error(`Файл ${file.name} не является корректным JSON`);
  }
}

export default function Setup({ meta, onMeta, onOpen, act, busy }: Props) {
  const [scenarioId, setScenarioId] = useState(meta.scenarios[0]?.id ?? "");
  const [goal, setGoal] = useState<Goal>("priority");
  const [planner, setPlanner] = useState(Object.keys(meta.planners)[0] ?? "");
  const [label, setLabel] = useState("");
  const [ov, setOv] = useState<Overrides>(EMPTY);
  const [showOv, setShowOv] = useState(false);
  const [sats, setSats] = useState<{ id: string; initial_soc_pct: number; capacity_wh: number }[]>([]);
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [title, setTitle] = useState("");

  const scenario = meta.scenarios.find((s) => s.id === scenarioId);
  const overrides = useMemo(() => buildOverrides(ov), [ov]);

  useEffect(() => {
    if (!scenarioId) return;
    api.scenario(scenarioId).then((s) => setSats((s.satellites as typeof sats) ?? [])).catch(() => setSats([]));
  }, [scenarioId]);

  const loadRuns = () => api.runs().then(setRuns).catch(() => setRuns([]));
  useEffect(() => { loadRuns(); }, []);

  const set = (k: keyof Overrides) => (e: { target: { value: string } }) => setOv({ ...ov, [k]: e.target.value });

  return (
    <div className="setup">
      <Card title="Новая смена">
        <p className="muted">Выберите сценарий, цель и алгоритм — затем откройте смену оператора.</p>
        <div className="scenario-grid">
          {meta.scenarios.map((s) => (
            <button key={s.id} type="button" className={`scenario-card ${s.id === scenarioId ? "selected" : ""}`}
              onClick={() => setScenarioId(s.id)}>
              <b>{s.title}</b>
              <div className="meta">
                <span>{s.satellites} КА</span>
                <span>{s.steps} шаг. ({fmt.time(s.steps)})</span>
                <span>{fmt.n(s.jobs)} заданий</span>
                {s.source === "custom" && <span className="badge">свой</span>}
              </div>
            </button>
          ))}
        </div>
        <div className="form-grid">
          <label>Цель управления
            <select value={goal} onChange={(e) => setGoal(e.target.value as Goal)}>
              {meta.goals.map((g) => <option key={g} value={g}>{GOAL_TEXT[g]}</option>)}
            </select>
          </label>
          <label>Алгоритм
            <select value={planner} onChange={(e) => setPlanner(e.target.value)}>
              {Object.entries(meta.planners).map(([p, v]) => <option key={p} value={p}>{p} {v}</option>)}
            </select>
          </label>
          <label>Название запуска
            <input value={label} placeholder="необязательно" onChange={(e) => setLabel(e.target.value)} />
          </label>
        </div>
        {scenario && (
          <p className="muted">
            {scenario.satellites} аппаратов · {scenario.steps} шагов ({fmt.time(scenario.steps)}) · {fmt.n(scenario.jobs)} заданий ·
            каналов с Землёй: {scenario.downlink_parallel_limit}
          </p>
        )}

        <button className="link" onClick={() => setShowOv(!showOv)}>
          {showOv ? "▾" : "▸"} Изменить начальные условия{overrides ? " (изменено)" : ""}
        </button>
        {showOv && (
          <div className="overrides">
            <p className="muted">Изменённые условия — новый эксперимент: расчёт начнётся с шага 0.</p>
            <div className="form-grid">
              <label>Начальный заряд аппарата
                <span className="row">
                  <select value={ov.socSat} onChange={set("socSat")}>
                    <option value="">—</option>
                    <option value="*">все аппараты</option>
                    {sats.map((s) => <option key={s.id} value={s.id}>{s.id} ({fmt.n(s.initial_soc_pct, 1)}%)</option>)}
                  </select>
                  <input type="number" min={0} max={100} step={1} placeholder="%" value={ov.socPct} onChange={set("socPct")} />
                </span>
              </label>
              <label>Солнечная мощность, множитель
                <input type="number" min={0} step={0.1} placeholder="1.0" value={ov.solar} onChange={set("solar")} />
              </label>
              <label>Приоритет задания
                <span className="row">
                  <input placeholder="ID задания" value={ov.jobId} onChange={set("jobId")} />
                  <select value={ov.jobPriority} onChange={set("jobPriority")}>
                    {[3, 2, 1].map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </span>
              </label>
              <label>Период недоступности
                <span className="row">
                  <select value={ov.failSat} onChange={set("failSat")}>
                    <option value="">—</option>
                    {sats.map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}
                  </select>
                  <input type="number" min={0} placeholder="с шага" value={ov.failStart} onChange={set("failStart")} />
                  <input type="number" min={1} placeholder="до шага" value={ov.failEnd} onChange={set("failEnd")} />
                </span>
              </label>
              <label>Каналов связи с Землёй
                <input type="number" min={1} placeholder={String(scenario?.downlink_parallel_limit ?? 2)} value={ov.dlLimit} onChange={set("dlLimit")} />
              </label>
            </div>
            <div className="row">
              <input placeholder="Название сохранённого сценария" value={title} onChange={(e) => setTitle(e.target.value)} />
              <button disabled={busy || !overrides} onClick={() => act(async () => {
                const s = await api.saveScenario({ base_id: scenarioId, overrides, title: title || `${scenario?.title} (изменён)` });
                onMeta();
                setScenarioId(s.id);
                setOv(EMPTY);
              })}>Сохранить как сценарий</button>
              <button className="link" onClick={() => setOv(EMPTY)}>Сбросить</button>
            </div>
          </div>
        )}

        <div className="row actions">
          <button className="primary" disabled={busy || !scenarioId} onClick={() => act(async () => {
            onOpen(await api.create({ scenario_id: scenarioId, goal, planner, label, overrides }));
          })}>Начать смену</button>
          <label className="file">Загрузить сценарий (JSON)
            <input type="file" accept=".json,application/json" onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) act(async () => {
                const s = await api.saveScenario({ scenario: await readJson(f), title: f.name.replace(/\.json$/, "") });
                onMeta();
                setScenarioId(s.id);
              });
            }} />
          </label>
          <label className="file">Открыть выгрузку расчёта
            <input type="file" accept=".json,application/json" onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) act(async () => {
                const r = await api.importResult(await readJson(f));
                const c = r.import_check as { summary_matches?: boolean } | undefined;
                if (c && c.summary_matches === false) alert("Внимание: пересчёт дал другую сводку, чем в файле.");
                onOpen(r);
              });
            }} />
          </label>
        </div>
      </Card>

      <Card title="Мои запуски" actions={<button onClick={loadRuns}>Обновить</button>}>
        <Table
          empty="Запусков пока нет."
          head={["Запуск", "Сценарий", "Цель", "Шаг", "Приоритет 3", "Выручка", "Создан", ""]}
          rows={runs.map((r) => [
            <span>{r.label || r.id}{r.parent && <span className="badge">ветвь</span>}</span>,
            r.scenario_title,
            GOAL_TEXT[r.goal],
            `${r.steps_executed}/${r.steps}`,
            `${fmt.n(r.brief.critical_jobs_completed_on_time as number)}/${fmt.n(r.brief.critical_jobs_due as number)}`,
            fmt.usd(r.brief.revenue_usd as number),
            new Date(r.created_at * 1000).toLocaleString("ru-RU"),
            <span className="row">
              <button disabled={busy} onClick={() => act(async () => onOpen(await api.run(r.id)))}>Открыть</button>
              <button className="danger" disabled={busy} onClick={() => act(async () => {
                if (!confirm(`Удалить запуск ${r.label || r.id}?`)) return;
                await api.remove(r.id);
                await loadRuns();
              })}>Удалить</button>
            </span>,
          ])}
        />
      </Card>
    </div>
  );
}
