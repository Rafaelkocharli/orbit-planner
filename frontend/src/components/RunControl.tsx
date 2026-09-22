import { useEffect, useState } from "react";
import { api, Goal, RunState, Task } from "../api";
import { fmt, GOAL_TEXT, Progress, Stat } from "./ui";

interface Props {
  run: RunState;
  onRun: (r: RunState) => void;
  act: (fn: () => Promise<void>) => void;
  busy: boolean;
  onClose: () => void;
}

export default function RunControl({ run, onRun, act, busy, onClose }: Props) {
  const [until, setUntil] = useState(Math.min(run.steps, run.step + 12));
  const [task, setTask] = useState<Task | null>(null);
  const s = run.summary;
  // Keep the stop step ahead of the current step after every advance.
  useEffect(() => setUntil((u) => (u <= run.step ? Math.min(run.steps, run.step + 12) : u)), [run.step, run.steps]);

  const advance = (body: { steps?: number; until_step?: number }) => act(async () => {
    try {
      onRun(await api.advance(run.run_id, body, setTask));
    } finally {
      setTask(null);
    }
  });

  const otherGoal: Goal = run.goal === "priority" ? "revenue" : "priority";

  return (
    <section className="runbar">
      <div className="runbar-head">
        <div>
          <h2>{run.label || run.scenario_title}</h2>
          <div className="muted">
            {run.scenario_title} · запуск <code>{run.run_id}</code> · {run.planner}
            {run.parent && "run_id" in run.parent && <> · ветвь от {String(run.parent.run_id)} с шага {String(run.parent.step)}</>}
          </div>
        </div>
        <button className="link" onClick={onClose}>← К списку</button>
      </div>

      <div className="stats">
        <Stat label="Шаг" value={`${run.step} / ${run.steps}`} sub={`${fmt.time(run.step, run.step_s)} модельного времени`} />
        <Stat label="Цель" value={GOAL_TEXT[run.goal]}
          sub={run.goal_history.length ? `смен цели: ${run.goal_history.length}` : undefined} />
        <Stat label="Приоритет 3 в срок" value={`${s.critical_jobs_completed_on_time} / ${s.critical_jobs_due}`}
          sub={s.critical_jobs_due ? fmt.pct(s.critical_jobs_completed_on_time / s.critical_jobs_due) : "срок ещё не наступил"} />
        <Stat label="Выручка" value={fmt.usd(s.revenue_usd)} sub={`выполнено ${s.jobs_completed} из ${s.jobs_total}`} />
        <Stat label="Просрочено" value={s.jobs_due_missed} tone={s.jobs_due_missed ? "bad" : undefined}
          sub={`работы впустую: ${s.work_steps_in_missed_jobs} шаг.`} />
        <Stat label="Мин. заряд" value={`${fmt.n(s.minimum_soc_pct, 1)}%`}
          tone={s.below_reserve_satellite_steps ? "bad" : undefined}
          sub={`ниже резерва: ${s.below_reserve_satellite_steps} шаг.`} />
      </div>

      <div className="row controls">
        <button disabled={busy || run.finished} onClick={() => advance({ steps: 1 })}>+1 шаг</button>
        <button disabled={busy || run.finished} onClick={() => advance({ steps: 12 })}>+1 час</button>
        <span className="row">
          <input type="number" min={run.step} max={run.steps} value={until}
            onChange={(e) => setUntil(Number(e.target.value))} aria-label="Остановиться перед шагом" />
          <button disabled={busy || run.finished || until <= run.step} onClick={() => advance({ until_step: until })}>
            До шага {until} ({fmt.time(until, run.step_s)})
          </button>
        </span>
        <button className="primary" disabled={busy || run.finished} onClick={() => advance({})}>До конца смены</button>
        <span className="sep" />
        <button disabled={busy || run.finished} title="Изменение запишется в историю и повлияет на дальнейшие шаги"
          onClick={() => act(async () => onRun(await api.goal(run.run_id, otherGoal)))}>
          Сменить цель → {GOAL_TEXT[otherGoal]}
        </button>
        <button disabled={busy} title="Независимая копия текущего состояния"
          onClick={() => act(async () => onRun(await api.fork(run.run_id, { label: `${run.label || run.scenario_id} · ветвь с шага ${run.step}` })))}>
          Ветвь отсюда
        </button>
        <span className="sep" />
        <button disabled={busy} onClick={() => act(() => api.downloadResult(run.run_id))}>Выгрузка JSON</button>
        <button disabled={busy} onClick={() => act(() => api.openReport(run.run_id))}>Отчёт</button>
      </div>
      {task && <Progress value={task.progress} text={`Расчёт: ${task.message || task.status}`} />}
      {run.finished && <p className="muted">Смена завершена. Можно сравнить варианты, разобрать потери или выгрузить результат.</p>}
    </section>
  );
}
