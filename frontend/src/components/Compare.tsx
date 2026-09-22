import { useEffect, useState } from "react";
import { api, Comparison, Goal, Meta, RunListItem, RunState, StrategyResult, Task } from "../api";
import { BriefCompare, Card, fmt, GOAL_TEXT, Progress, Table, VerdictBox } from "./ui";

interface Props {
  run: RunState;
  meta: Meta;
  onOpen: (r: RunState) => void;
  act: (fn: () => Promise<void>) => void;
  busy: boolean;
}

export default function Compare({ run, meta, onOpen, act, busy }: Props) {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [a, setA] = useState(run.run_id);
  const [b, setB] = useState("");
  const [goal, setGoal] = useState<Goal>(run.goal);
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [strat, setStrat] = useState<StrategyResult | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  // Default: the main planner with both goals plus the simple rule as the baseline.
  const [cands, setCands] = useState<string[]>(() => {
    const main = Object.keys(meta.planners)[0];
    const base = "greedy-edf" in meta.planners ? [`greedy-edf|${run.goal}`] : [];
    return [...meta.goals.map((g) => `${main}|${g}`), ...base];
  });

  const loadRuns = () => api.runs().then((rs) => {
    setRuns(rs);
    if (!b) setB(rs.find((r) => r.id !== run.run_id && r.scenario_id === run.scenario_id)?.id ?? "");
  });
  useEffect(() => { loadRuns(); setA(run.run_id); }, [run.run_id]);

  const label = (r: RunListItem) =>
    `${r.label || r.id} · ${GOAL_TEXT[r.goal]} · шаг ${r.steps_executed}/${r.steps}${r.parent ? " · ветвь" : ""}`;

  return (
    <>
      <Card title="Ветви из текущего состояния">
        <p className="muted">
          Продолжить смену с шага {run.step} несколькими стратегиями. Условия одинаковы: то же состояние, те же сообщения.
          Каждая ветвь сохраняется как отдельный запуск.
        </p>
        <div className="sat-picker">
          {Object.keys(meta.planners).flatMap((p) => meta.goals.map((g) => {
            const key = `${p}|${g}`;
            return (
              <button key={key} className={cands.includes(key) ? "chip on" : "chip"}
                onClick={() => setCands(cands.includes(key) ? cands.filter((c) => c !== key) : [...cands, key])}>
                {p} · {GOAL_TEXT[g]}
              </button>
            );
          }))}
        </div>
        <div className="row">
          <label className="inline">Сравнивать по цели
            <select value={goal} onChange={(e) => setGoal(e.target.value as Goal)}>
              {meta.goals.map((g) => <option key={g} value={g}>{GOAL_TEXT[g]}</option>)}
            </select>
          </label>
          <button className="primary" disabled={busy || run.finished || cands.length === 0} onClick={() => act(async () => {
            try {
              const r = await api.strategies(run.run_id, {
                rank_goal: goal,
                candidates: cands.map((c) => {
                  const [planner, g] = c.split("|");
                  return { planner, goal: g, label: `${planner} · ${GOAL_TEXT[g]} · с шага ${run.step}` };
                }),
              }, setTask);
              setStrat(r);
              await loadRuns();
            } finally { setTask(null); }
          })}>Рассчитать ветви</button>
        </div>
        {run.finished && <p className="muted">Смена завершена — ветвить нечего. Откройте запуск на более раннем шаге или сравните готовые запуски ниже.</p>}
        {task && <Progress value={task.progress} text={`Досчитываю ветви: ${task.message}`} />}
        {strat && (
          <>
            <VerdictBox v={strat.verdict_vs_runner_up} />
            <Table
              head={["#", "Стратегия", "Приоритет 3 в срок", "Выручка", "Выполнено", "Просрочено", "Ср. конечный заряд", ""]}
              rows={strat.ranking.map((r, i) => [i + 1, r.label,
                `${r.metrics.critical_jobs_completed_on_time}/${r.metrics.critical_jobs_due}`, fmt.usd(r.metrics.revenue_usd),
                r.metrics.jobs_completed, r.metrics.jobs_due_missed, `${fmt.n(r.metrics.mean_terminal_soc_pct, 1)}%`,
                <span className="row">
                  <button disabled={busy} onClick={() => act(async () => onOpen(await api.run(r.run_id)))}>Открыть</button>
                  <button disabled={busy} onClick={() => { setA(r.run_id); setB(strat.ranking[i === 0 ? 1 : 0]?.run_id ?? ""); }}>В сравнение</button>
                </span>])} />
            <p className="muted">{strat.note}</p>
          </>
        )}
      </Card>

      <Card title="Сравнить два запуска">
        <div className="row">
          <label className="inline">A
            <select value={a} onChange={(e) => setA(e.target.value)}>
              {runs.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
            </select>
          </label>
          <label className="inline">B
            <select value={b} onChange={(e) => setB(e.target.value)}>
              <option value="">—</option>
              {runs.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
            </select>
          </label>
          <label className="inline">Цель
            <select value={goal} onChange={(e) => setGoal(e.target.value as Goal)}>
              {meta.goals.map((g) => <option key={g} value={g}>{GOAL_TEXT[g]}</option>)}
            </select>
          </label>
          <button disabled={busy || !a || !b || a === b} onClick={() => act(async () => setCmp(await api.compare(a, b, goal)))}>Сравнить</button>
        </div>
        {cmp && (
          <>
            {!cmp.same_conditions && <p className="bad">Условия различаются — сравнение отражает разные исходные данные.</p>}
            {cmp.notes.map((n) => <p key={n} className="muted">{n}</p>)}
            {cmp.origin && <p className="muted">Обе ветви начинаются из одного состояния: запуск {String(cmp.origin.from)}, шаг {String(cmp.origin.step)}.</p>}
            <VerdictBox v={cmp.verdict} />
            <BriefCompare cols={[{ name: `A: ${cmp.a.label || cmp.a.run_id}`, brief: cmp.metrics.a }, { name: `B: ${cmp.b.label || cmp.b.run_id}`, brief: cmp.metrics.b }]} />
            <div className="grid2">
              <div>
                <h4>Выполнены только в A: {cmp.only_a_completed.count} ({fmt.usd(cmp.only_a_completed.value_usd)}, приоритет 3: {cmp.only_a_completed.critical})</h4>
                <p className="muted small">{cmp.only_a_completed.items.map((j) => `${j.id} (п${j.priority})`).join(", ") || "—"}</p>
              </div>
              <div>
                <h4>Выполнены только в B: {cmp.only_b_completed.count} ({fmt.usd(cmp.only_b_completed.value_usd)}, приоритет 3: {cmp.only_b_completed.critical})</h4>
                <p className="muted small">{cmp.only_b_completed.items.map((j) => `${j.id} (п${j.priority})`).join(", ") || "—"}</p>
              </div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
