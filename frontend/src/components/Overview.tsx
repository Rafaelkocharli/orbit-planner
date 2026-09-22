import { useEffect, useState } from "react";
import { Analytics, api, Forecast, PlannerReport, RunState, Task } from "../api";
import { BriefCompare, Card, fmt, Progress, Table } from "./ui";

const REPLAN_TEXT: Record<string, string> = {
  start: "начало смены", periodic: "плановый", event: "новое сообщение", goal_changed: "смена цели", deviation: "отклонение от плана",
};

export default function Overview({ run, act, busy }: { run: RunState; act: (fn: () => Promise<void>) => void; busy: boolean }) {
  const [a, setA] = useState<Analytics | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [planner, setPlanner] = useState<PlannerReport | null>(null);

  useEffect(() => {
    api.analytics(run.run_id).then(setA).catch(() => setA(null));
    api.planner(run.run_id).then(setPlanner).catch(() => setPlanner(null));
    setForecast(null);
  }, [run.run_id, run.step, run.events.length, run.goal]);

  const sats = Object.entries(run.satellites).sort(([a], [b]) => a.localeCompare(b));
  const reserve = 30;

  return (
    <>
    <Card title="Состояние группировки">
      <div className="fleet-grid">
        {sats.map(([id, s]) => (
          <div key={id} className={`fleet-tile ${s.available ? "" : "off"}`}>
            <div className="id">{id}</div>
            <div className="soc-line"><i className={s.soc_pct < reserve ? "low" : ""} style={{ width: `${Math.min(100, s.soc_pct)}%` }} /></div>
            <div className="tiny">
              {fmt.n(s.soc_pct, 0)}% · {fmt.n(s.temp_c, 0)}°C
              {!s.available && <span className="bad"> · нет</span>}
              {s.calibration_left_steps === 0 && <span className="bad"> · калибр.</span>}
            </div>
          </div>
        ))}
      </div>
      <p className="muted">Заряд и температура на текущем шаге. Подробные ряды — на вкладке «Аппараты».</p>
    </Card>
    <div className="grid2">
      <Card title="Результат по приоритетам">
        {a && <Table
          head={["Приоритет", "Всего", "Срок наступил", "В срок", "Доля", "Выручка", "Потеряно"]}
          rows={a.by_priority.map((p) => [p.priority, p.total, p.due, p.completed_on_time_of_due, fmt.pct(p.rate),
            fmt.usd(p.revenue_usd), p.value_lost_usd ? <span className="bad">{fmt.usd(p.value_lost_usd)}</span> : "—"])} />}
        {a && <p className="muted">Загрузка группировки: {fmt.pct(a.utilization.fleet_utilization)} шагов заняты заданиями.
          Доля не считается, пока нет выполненных шагов или заданий со сроком.</p>}
      </Card>

      {planner?.report && (
        <Card title={`Как работал алгоритм: ${planner.planner} ${planner.version}`}>
          <p>
            Пересчётов плана: <strong>{planner.report.replans}</strong>, из них с доказанной оптимальностью на горизонте:{" "}
            <strong>{planner.report.proven_optimal}</strong>
            {planner.report.fallbacks > 0 && <span className="bad"> · запасное правило: {planner.report.fallbacks}</span>}
          </p>
          <p className="muted">
            Причины: {Object.entries(planner.report.by_reason).map(([r, n]) => `${REPLAN_TEXT[r] ?? r} — ${n}`).join(", ")}.
            Горизонт {String(planner.params.horizon)} шагов, пересчёт каждые {String(planner.params.replan_every)}.
          </p>
          <Table dense
            head={["Шаг", "Причина", "Статус", "Заданий в задаче", "Запланировано завершить", "Разрыв с границей", "Время, с"]}
            rows={planner.report.solves.slice(-12).reverse().map((x) => [x.step, REPLAN_TEXT[x.reason] ?? x.reason,
              x.status === "OPTIMAL" || x.gap === 0 ? <span className="good">оптимум</span> : x.status === "FEASIBLE" ? "допустимое" : <span className="bad">{x.status}</span>,
              x.jobs_considered, x.planned_completions ?? "—", x.gap === undefined ? "—" : fmt.pct(x.gap, 2), fmt.n(x.wall_s, 1)])} />
        </Card>
      )}

      <Card title="Прогноз до конца смены" actions={
        <button disabled={busy || run.finished} onClick={() => act(async () => {
          try { setForecast(await api.forecast(run.run_id, setTask)); } finally { setTask(null); }
        })}>Рассчитать прогноз</button>}>
        {task && <Progress value={task.progress} text="Досчитываю копию смены…" />}
        {run.finished && <p className="muted">Смена завершена — прогноз не нужен.</p>}
        {!forecast && !run.finished && !task && <p className="muted">Прогноз досчитывает копию смены текущей стратегией и предупреждает о проблемах. Запуск не меняется.</p>}
        {forecast && (
          <>
            <BriefCompare cols={[{ name: `Факт (шаг ${forecast.as_of_step})`, brief: forecast.current }, { name: "Прогноз к концу", brief: forecast.projected }]} />
            <h4>Предупреждения ({forecast.warnings.length})</h4>
            {forecast.warnings.length ? (
              <ul className="warnings">
                {forecast.warnings.slice(0, 40).map((w, i) => (
                  <li key={i} className={w.type}>
                    <span className="badge">{w.in_steps <= 0 ? "сейчас" : `через ${w.in_steps} шаг.`}</span> {w.message}
                  </li>
                ))}
                {forecast.warnings.length > 40 && <li className="muted">и ещё {forecast.warnings.length - 40}</li>}
              </ul>
            ) : <p className="good">Проблем не ожидается.</p>}
            <p className="muted">{forecast.note}</p>
          </>
        )}
      </Card>
    </div>
    </>
  );
}
