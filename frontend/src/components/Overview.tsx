import { useEffect, useState } from "react";
import { Analytics, api, Forecast, RunState, Task } from "../api";
import { BriefCompare, Card, fmt, Progress, Table } from "./ui";

export default function Overview({ run, act, busy }: { run: RunState; act: (fn: () => Promise<void>) => void; busy: boolean }) {
  const [a, setA] = useState<Analytics | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [task, setTask] = useState<Task | null>(null);

  useEffect(() => {
    api.analytics(run.run_id).then(setA).catch(() => setA(null));
    setForecast(null);
  }, [run.run_id, run.step, run.events.length, run.goal]);

  return (
    <div className="grid2">
      <Card title="Результат по приоритетам">
        {a && <Table
          head={["Приоритет", "Всего", "Срок наступил", "В срок", "Доля", "Выручка", "Потеряно"]}
          rows={a.by_priority.map((p) => [p.priority, p.total, p.due, p.completed_on_time_of_due, fmt.pct(p.rate),
            fmt.usd(p.revenue_usd), p.value_lost_usd ? <span className="bad">{fmt.usd(p.value_lost_usd)}</span> : "—"])} />}
        {a && <p className="muted">Загрузка группировки: {fmt.pct(a.utilization.fleet_utilization)} шагов заняты заданиями.
          Доля не считается, пока нет выполненных шагов или заданий со сроком.</p>}
      </Card>

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
  );
}
