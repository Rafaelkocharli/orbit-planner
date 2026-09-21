import { useEffect, useState } from "react";
import { api, Meta, RunState } from "./api";

// Skeleton operator screen: setup → run control → events → state → compare/export.
// Split into components (Setup, Timeline, Satellites, Jobs, Compare) as the UI grows.
export default function App() {
  const [meta, setMeta] = useState<Meta>();
  const [scenario, setScenario] = useState("P01_intro");
  const [goal, setGoal] = useState("priority");
  const [run, setRun] = useState<RunState>();
  const [branch, setBranch] = useState<RunState>();
  const [until, setUntil] = useState(0);
  const [eventText, setEventText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(e.message));
  }, []);

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>Orbit Planner</h1>

      <section>
        <h2>Смена</h2>
        <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
          {meta?.scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title} — {s.satellites} КА, {s.jobs} заданий
            </option>
          ))}
        </select>
        <select value={goal} onChange={(e) => setGoal(e.target.value)}>
          <option value="priority">Приоритетное обслуживание</option>
          <option value="revenue">Коммерческая отдача</option>
        </select>
        <button disabled={busy} onClick={() => act(async () => {
          setRun(await api.create({ scenario_id: scenario, goal }));
          setBranch(undefined);
        })}>
          Новый расчёт
        </button>
      </section>

      {run && (
        <>
          <section>
            <h2>Ход расчёта: шаг {run.step} / {run.steps} · цель {run.goal}</h2>
            <button disabled={busy || run.finished} onClick={() => act(async () => setRun(await api.advance(run.run_id, { steps: 1 })))}>+1 шаг</button>
            <input type="number" value={until} onChange={(e) => setUntil(+e.target.value)} />
            <button disabled={busy || run.finished} onClick={() => act(async () => setRun(await api.advance(run.run_id, { until_step: until })))}>До шага</button>
            <button disabled={busy || run.finished} onClick={() => act(async () => setRun(await api.advance(run.run_id, {})))}>До конца</button>
            <button disabled={busy} onClick={() => act(async () => setRun(await api.goal(run.run_id, run.goal === "priority" ? "revenue" : "priority")))}>Сменить цель</button>
            <a href={api.resultUrl(run.run_id)} download={`result-${run.run_id}.json`}>Выгрузить JSON</a>
          </section>

          <section>
            <h2>Сообщение (at_step = {run.step})</h2>
            <textarea rows={6} value={eventText} onChange={(e) => setEventText(e.target.value)}
              placeholder='{"id": "E-1", "at_step": 72, "type": "satellite_outage", "satellite_ids": ["S08"], "end_step": 90}' />
            <button disabled={busy} onClick={() => act(async () => setRun(await api.event(run.run_id, JSON.parse(eventText))))}>Применить</button>
          </section>

          <section>
            <h2>Сравнение</h2>
            <button disabled={busy} onClick={() => act(async () => {
              const b = await api.fork(run.run_id, { goal: run.goal === "priority" ? "revenue" : "priority" });
              setBranch(await api.advance(b.run_id, {}));
            })}>
              Ветвь с другой целью до конца
            </button>
            <SummaryTable a={run} b={branch} />
          </section>

          <section>
            <h2>Аппараты</h2>
            <table>
              <thead><tr><th>КА</th><th>SOC %</th><th>T °C</th><th>Калибровка, шагов</th><th>Доступен</th></tr></thead>
              <tbody>
                {Object.entries(run.satellites).map(([sid, s]) => (
                  <tr key={sid}>
                    <td>{sid}</td><td>{s.soc_pct.toFixed(1)}</td><td>{s.temp_c.toFixed(1)}</td>
                    <td>{s.calibration_age_steps}</td><td>{s.available ? "да" : "нет"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}

      {busy && <p className="status">Расчёт…</p>}
      {error && <p className="error">{error}</p>}
    </main>
  );
}

function SummaryTable({ a, b }: { a: RunState; b?: RunState }) {
  const keys = Object.keys(a.summary).filter((k) => typeof a.summary[k] !== "object");
  return (
    <table>
      <thead><tr><th>Показатель</th><th>{a.goal} (шаг {a.step})</th>{b && <th>{b.goal} (ветвь)</th>}</tr></thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k}><td>{k}</td><td>{String(a.summary[k])}</td>{b && <td>{String(b.summary[k])}</td>}</tr>
        ))}
      </tbody>
    </table>
  );
}
