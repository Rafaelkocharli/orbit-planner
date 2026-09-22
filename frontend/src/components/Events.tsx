import { useState } from "react";
import { api, Dict, RunState, Task, WhatIfEventResult } from "../api";
import { BriefCompare, Card, fmt, Progress, Table, VerdictBox } from "./ui";

interface Props {
  run: RunState;
  onRun: (r: RunState) => void;
  act: (fn: () => Promise<void>) => void;
  busy: boolean;
}

type Kind = "satellite_outage" | "close_downlink" | "add_jobs" | "raw";

interface JobDraft {
  id: string;
  kind: "relay" | "downlink";
  release_step: number;
  deadline_step: number;
  work_steps: number;
  eligible_satellites: string[];
  priority: number;
  value_usd: number;
}

const TYPE_TEXT: Record<string, string> = {
  add_jobs: "Новые задания",
  satellite_outage: "Недоступность аппаратов",
  close_downlink: "Отмена сеансов связи с Землёй",
};

export default function Events({ run, onRun, act, busy }: Props) {
  const sats = Object.keys(run.satellites).sort();
  const [kind, setKind] = useState<Kind>("satellite_outage");
  const [selected, setSelected] = useState<string[]>([]);
  const [endStep, setEndStep] = useState(Math.min(run.steps, run.step + 12));
  const [jobs, setJobs] = useState<JobDraft[]>([]);
  const [draft, setDraft] = useState<JobDraft>(() => newJob(run, 1));
  const [raw, setRaw] = useState("");
  const [preview, setPreview] = useState<WhatIfEventResult | null>(null);
  const [task, setTask] = useState<Task | null>(null);

  const build = (): Dict => {
    if (kind === "raw") {
      try {
        return JSON.parse(raw);
      } catch {
        throw new Error("Сообщение должно быть корректным JSON-объектом");
      }
    }
    if (kind === "add_jobs") {
      if (!jobs.length) throw new Error("Добавьте хотя бы одно задание");
      return { type: "add_jobs", jobs };
    }
    if (!selected.length) throw new Error("Выберите аппараты");
    return { type: kind, satellite_ids: selected, end_step: endStep };
  };

  const toggle = (sid: string) =>
    setSelected(selected.includes(sid) ? selected.filter((x) => x !== sid) : [...selected, sid]);

  const reset = () => {
    setSelected([]);
    setJobs([]);
    setDraft(newJob(run, 1));
    setRaw("");
    setPreview(null);
  };

  return (
    <Card title={`Сообщение на шаге ${run.step} (${fmt.time(run.step, run.step_s)})`}>
      {run.finished ? <p className="muted">Смена завершена — сообщения больше не принимаются.</p> : <>
        <div className="row">
          {(["satellite_outage", "close_downlink", "add_jobs", "raw"] as Kind[]).map((k) => (
            <label key={k} className="radio">
              <input type="radio" checked={kind === k} onChange={() => { setKind(k); setPreview(null); }} />
              {k === "raw" ? "JSON" : TYPE_TEXT[k]}
            </label>
          ))}
        </div>

        {(kind === "satellite_outage" || kind === "close_downlink") && (
          <>
            <div className="sat-picker">
              {sats.map((sid) => (
                <button key={sid} className={selected.includes(sid) ? "chip on" : "chip"} onClick={() => toggle(sid)}>{sid}</button>
              ))}
              <button className="link" onClick={() => setSelected(selected.length === sats.length ? [] : sats)}>
                {selected.length === sats.length ? "снять все" : "выбрать все"}
              </button>
            </div>
            <label className="inline">До шага (не включая)
              <input type="number" min={run.step + 1} max={run.steps} value={endStep} onChange={(e) => setEndStep(Number(e.target.value))} />
              <span className="muted">{fmt.time(endStep, run.step_s)}</span>
            </label>
          </>
        )}

        {kind === "add_jobs" && (
          <>
            <div className="form-grid compact">
              <label>ID<input value={draft.id} onChange={(e) => setDraft({ ...draft, id: e.target.value })} /></label>
              <label>Тип
                <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as JobDraft["kind"], eligible_satellites: draft.eligible_satellites.slice(0, e.target.value === "downlink" ? 1 : undefined) })}>
                  <option value="relay">relay — ретрансляция</option>
                  <option value="downlink">downlink — передача на Землю</option>
                </select>
              </label>
              <label>Начало<input type="number" min={run.step} value={draft.release_step} onChange={(e) => setDraft({ ...draft, release_step: Number(e.target.value) })} /></label>
              <label>Срок<input type="number" max={run.steps} value={draft.deadline_step} onChange={(e) => setDraft({ ...draft, deadline_step: Number(e.target.value) })} /></label>
              <label>Шагов работы<input type="number" min={1} value={draft.work_steps} onChange={(e) => setDraft({ ...draft, work_steps: Number(e.target.value) })} /></label>
              <label>Приоритет
                <select value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })}>
                  {[3, 2, 1].map((p) => <option key={p}>{p}</option>)}
                </select>
              </label>
              <label>Стоимость, $<input type="number" min={0} value={draft.value_usd} onChange={(e) => setDraft({ ...draft, value_usd: Number(e.target.value) })} /></label>
            </div>
            <div className="sat-picker">
              <span className="muted">Исполнители{draft.kind === "downlink" ? " (ровно один)" : ""}:</span>
              {sats.map((sid) => (
                <button key={sid} className={draft.eligible_satellites.includes(sid) ? "chip on" : "chip"} onClick={() => {
                  const has = draft.eligible_satellites.includes(sid);
                  const next = draft.kind === "downlink" ? (has ? [] : [sid])
                    : has ? draft.eligible_satellites.filter((x) => x !== sid) : [...draft.eligible_satellites, sid];
                  setDraft({ ...draft, eligible_satellites: next });
                }}>{sid}</button>
              ))}
            </div>
            <button disabled={!draft.id || !draft.eligible_satellites.length} onClick={() => {
              setJobs([...jobs, draft]);
              setDraft(newJob(run, jobs.length + 2));
            }}>Добавить задание в сообщение</button>
            <Table dense empty="Заданий в сообщении нет."
              head={["ID", "Тип", "Окно", "Работа", "Исполнители", "Приоритет", "$", ""]}
              rows={jobs.map((j, i) => [j.id, j.kind, `${j.release_step}–${j.deadline_step}`, j.work_steps,
                j.eligible_satellites.join(", "), j.priority, j.value_usd,
                <button className="link" onClick={() => setJobs(jobs.filter((_, k) => k !== i))}>убрать</button>])} />
          </>
        )}

        {kind === "raw" && (
          <textarea rows={8} value={raw} onChange={(e) => setRaw(e.target.value)} spellCheck={false}
            placeholder={`{"id": "E-01", "at_step": ${run.step}, "type": "satellite_outage", "satellite_ids": ["S08"], "end_step": ${run.step + 16}}`} />
        )}

        <div className="row actions">
          <button className="primary" disabled={busy} onClick={() => act(async () => {
            onRun(await api.event(run.run_id, build()));
            reset();
          })}>Применить</button>
          <button disabled={busy} title="Досчитать смену с сообщением и без него, не меняя запуск"
            onClick={() => act(async () => {
              try {
                setPreview(await api.whatIfEvent(run.run_id, build(), setTask));
              } finally {
                setTask(null);
              }
            })}>Оценить последствия</button>
          <button className="link" onClick={reset}>Очистить</button>
        </div>
        {task && <Progress value={task.progress} text="Досчитываю два варианта…" />}
        {preview && (
          <div className="preview">
            <p><strong>{preview.summary}</strong></p>
            <VerdictBox v={preview.verdict} />
            <BriefCompare cols={[{ name: "Без сообщения", brief: preview.without }, { name: "С сообщением", brief: preview.with }]} />
            {preview.displaced_jobs.length > 0 && (
              <p className="muted">Вытеснены: {preview.displaced_jobs.slice(0, 20).map((j) => `${j.id} (п${j.priority}, $${j.value_usd})`).join(", ")}
                {preview.displaced_jobs.length > 20 && ` и ещё ${preview.displaced_jobs.length - 20}`}</p>
            )}
            <p className="muted">{preview.note}</p>
          </div>
        )}
      </>}

      <h4>Полученные сообщения</h4>
      <Table dense empty="Сообщений не было."
        head={["Шаг", "Время", "ID", "Тип", "Содержание"]}
        rows={run.events.map((e) => [e.at_step, fmt.time(e.at_step, run.step_s), e.id, TYPE_TEXT[e.type] ?? e.type,
          e.type === "add_jobs" ? (e.jobs as { id: string }[]).map((j) => j.id).join(", ")
            : `${(e.satellite_ids as string[]).join(", ")} до шага ${e.end_step}`])} />
    </Card>
  );
}

function newJob(run: RunState, n: number): JobDraft {
  return {
    id: `OP-JOB-${run.step}-${n}`, kind: "relay", release_step: run.step,
    deadline_step: Math.min(run.steps, run.step + 8), work_steps: 2,
    eligible_satellites: [], priority: 3, value_usd: 40,
  };
}
