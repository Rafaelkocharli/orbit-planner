import { useState } from "react";
import { api, Dict, RunState, Task, WhatIfResourcesResult } from "../api";
import { BriefCompare, Card, Progress, VerdictBox } from "./ui";

const PRESETS: { id: string; label: string; overrides: Dict }[] = [
  { id: "dl3", label: "3 канала связи с Землёй", overrides: { downlink_parallel_limit: 3 } },
  { id: "dl4", label: "4 канала связи с Землёй", overrides: { downlink_parallel_limit: 4 } },
  { id: "sun12", label: "Солнечная мощность ×1.2", overrides: { solar_factor: 1.2 } },
  { id: "sun06", label: "Солнечная мощность ×0.6", overrides: { solar_factor: 0.6 } },
  { id: "soc90", label: "Все аппараты стартуют с 90%", overrides: { initial_soc: { "*": 90 } } },
];

export default function WhatIf({ run, act, busy }: { run: RunState; act: (fn: () => Promise<void>) => void; busy: boolean }) {
  const [picked, setPicked] = useState<string[]>(["dl3", "sun12"]);
  const [res, setRes] = useState<WhatIfResourcesResult | null>(null);
  const [task, setTask] = useState<Task | null>(null);

  return (
    <Card title="Что даст дополнительный ресурс">
      <p className="muted">
        Каждый вариант — новый эксперимент с шага 0 с той же стратегией и теми же сообщениями, что получил этот запуск.
        Показывает, какой ресурс сильнее всего ограничивает результат.
      </p>
      <div className="sat-picker">
        {PRESETS.map((p) => (
          <button key={p.id} className={picked.includes(p.id) ? "chip on" : "chip"}
            onClick={() => setPicked(picked.includes(p.id) ? picked.filter((x) => x !== p.id) : [...picked, p.id])}>{p.label}</button>
        ))}
      </div>
      <button className="primary" disabled={busy || !picked.length} onClick={() => act(async () => {
        try {
          setRes(await api.whatIfResources(run.run_id,
            PRESETS.filter((p) => picked.includes(p.id)).map((p) => ({ label: p.label, overrides: p.overrides })), setTask));
        } finally { setTask(null); }
      })}>Рассчитать</button>
      {task && <Progress value={task.progress} text={`Считаю варианты: ${task.message}`} />}
      {res && (
        <>
          <BriefCompare cols={res.results.map((r) => ({ name: r.label, brief: r.metrics }))} />
          {res.results.slice(1).map((r) => (
            <div key={r.label}>
              <h4>{r.label}</h4>
              <VerdictBox v={r.verdict} />
              {r.event_errors.length > 0 && <p className="bad">Не применились сообщения: {r.event_errors.map((e) => String(e.event_id)).join(", ")}</p>}
            </div>
          ))}
          <p className="muted">{res.note}</p>
        </>
      )}
    </Card>
  );
}
