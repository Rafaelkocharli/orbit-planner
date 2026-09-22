import { useEffect, useState } from "react";
import { Analytics, api, Dict, RunState } from "../api";
import Explain from "./Explain";
import { WhyCard } from "./Jobs";
import { CAUSE_TEXT, Card, fmt, REASON_TEXT, Table } from "./ui";

export default function Analysis({ run }: { run: RunState }) {
  const [a, setA] = useState<Analytics | null>(null);
  const [cause, setCause] = useState("");
  const [why, setWhy] = useState<Dict | null>(null);
  const sids = Object.keys(run.satellites).sort();
  const [exStep, setExStep] = useState(Math.max(0, run.step - 1));
  const [exSat, setExSat] = useState(sids[0]);
  const [explain, setExplain] = useState<{ step: number; sid: string } | null>(null);

  useEffect(() => {
    api.analytics(run.run_id).then(setA).catch(() => setA(null));
  }, [run.run_id, run.step, run.events.length]);

  if (!a) return <p className="muted">Загрузка аналитики…</p>;
  const missed = a.missed.jobs.filter((j) => !cause || j.primary === cause);
  const maxJobs = Math.max(1, ...a.missed.by_cause.map((c) => c.jobs));
  const avoidable = a.missed.by_cause.filter((c) => c.avoidable).reduce((s, c) => s + c.jobs, 0);

  return (
    <>
      <div className="grid2">
        <Card title={`Причины невыполнения (${a.missed.count})`}>
          {a.missed.count === 0 ? <p className="good">Просроченных заданий нет.</p> : (
            <>
              <p>Неустранимо: <strong>{a.missed.count - avoidable}</strong> · устранимо: <strong>{avoidable}</strong></p>
              <div className="bars">
                {a.missed.by_cause.map((c) => (
                  <button key={c.cause} className={`barrow ${cause === c.cause ? "on" : ""}`} onClick={() => setCause(cause === c.cause ? "" : c.cause)} title={c.text}>
                    <span className="bl">{CAUSE_TEXT[c.cause] ?? c.cause}</span>
                    <span className="bt"><i className={c.avoidable ? "" : "fixed"} style={{ width: `${(100 * c.jobs) / maxJobs}%` }} /></span>
                    <span className="bv">{c.jobs} · {fmt.usd(c.value_usd)}</span>
                  </button>
                ))}
              </div>
              <p className="muted">Серым — доказуемо невыполнимые задания. Нажмите на причину, чтобы отфильтровать список.</p>
            </>
          )}
        </Card>

        <Card title="Разобрать решение">
          <div className="row">
            <label className="inline">Шаг
              <input type="number" min={0} max={Math.max(0, run.step - 1)} value={exStep} onChange={(e) => setExStep(Number(e.target.value))} />
            </label>
            <label className="inline">Аппарат
              <select value={exSat} onChange={(e) => setExSat(e.target.value)}>
                {sids.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <button disabled={run.step === 0} onClick={() => setExplain({ step: exStep, sid: exSat })}>Разобрать</button>
          </div>
          <p className="muted">Что было известно на шаге, какие действия были допустимы и почему выбрано именно это.</p>
        </Card>
      </div>

      {explain && <Explain run={run} step={explain.step} sid={explain.sid} onClose={() => setExplain(null)} />}
      {why && <WhyCard why={why} onClose={() => setWhy(null)} />}

      <Card title={`Просроченные задания${cause ? `: ${CAUSE_TEXT[cause]}` : ""} (${missed.length})`}>
        <Table dense empty="Нет."
          head={["ID", "Тип", "П", "$", "Окно", "Сделано", "Причина", "Обоснование / конкуренты", ""]}
          rows={missed.slice(0, 200).map((j) => [
            j.id, j.kind, j.priority, fmt.n(j.value_usd, 2), `${j.window[0]}–${j.window[1]}`, `${j.work_done}/${j.work_steps}`,
            CAUSE_TEXT[j.primary] ?? j.primary,
            j.proof ? j.proof.statement : j.competing_jobs ? j.competing_jobs.slice(0, 3).map((c) => `${c.id} (п${c.priority})`).join(", ") : "",
            <button className="link" onClick={() => api.why(run.run_id, j.id).then(setWhy)}>подробнее</button>,
          ])} />
      </Card>

      <div className="grid2">
        <Card title={`Периоды дефицита энергии (${a.deficit_periods.length})`}>
          <Table dense empty="Заряд не опускался ниже резерва."
            head={["КА", "Шаги", "Время", "Мин. заряд", "Отметки"]}
            rows={a.deficit_periods.slice(0, 100).map((d) => [d.satellite_id, `${d.start_step}–${d.end_step}`,
              `${fmt.time(d.start_step, run.step_s)}–${fmt.time(d.end_step, run.step_s)}`, `${fmt.n(d.min_soc_pct, 1)}%`,
              [d.critical && "ниже критического", d.brownout && "нехватка питания"].filter(Boolean).join(", ")])} />
        </Card>
        <Card title={`Отклонённые команды (${a.blocked_commands.count})`}>
          <Table dense empty="Отклонённых команд нет."
            head={["Причина", "Количество"]}
            rows={Object.entries(a.blocked_commands.by_reason).map(([r, n]) => [REASON_TEXT[r] ?? r, n])} />
          {a.blocked_commands.items.length > 0 && (
            <Table dense head={["Шаг", "КА", "Команда", "Причина"]}
              rows={a.blocked_commands.items.slice(0, 30).map((b) => [b.step, b.satellite_id,
                String((b.requested as { job_id?: string; action?: string }).job_id ?? b.requested.action), REASON_TEXT[b.reason] ?? b.reason])} />
          )}
        </Card>
      </div>

      <Card title={`Загрузка аппаратов · в среднем ${fmt.pct(a.utilization.fleet_utilization)}`}>
        <Table dense
          head={["КА", "Задания", "Калибровка", "Ожидание", "Отклонено", "Недоступен", "Загрузка"]}
          rows={Object.entries(a.utilization.satellites).sort().map(([sid, u]) => [sid, u.job_steps, u.calibrate_steps,
            u.idle_steps, u.blocked_commands, u.unavailable_steps,
            <span className="soc"><span className="bar" style={{ width: `${100 * (u.utilization ?? 0)}%` }} />{fmt.pct(u.utilization)}</span>])} />
      </Card>
    </>
  );
}
