import { useEffect, useState } from "react";
import { api, Explain as ExplainData, RunState } from "../api";
import { Card, fmt, Modal, REASON_TEXT, Table } from "./ui";

/** Why a satellite did what it did at a step: known info, state, options, consequences. */
export default function Explain({ run, step, sid, onClose }: { run: RunState; step: number; sid: string; onClose?: () => void }) {
  const [data, setData] = useState<ExplainData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setData(null);
    setError("");
    api.explain(run.run_id, step, sid).then(setData).catch((e) => setError(e.message));
  }, [run.run_id, step, sid]);

  const st = data?.state_before as Record<string, number | boolean | string[]> | undefined;
  const req = data?.decision.requested as { action?: string; job_id?: string } | undefined;
  const title = `Решение: ${sid}, шаг ${step} (${fmt.time(step, run.step_s)})`;
  const body = (
    <>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Восстанавливаю состояние на шаге {step}…</p>}
      {data && st && (
        <>
          <p>
            <strong>Выполнено:</strong> {data.decision.executed === "job" ? `задание ${req?.job_id}` : data.decision.executed === "calibrate" ? "калибровка" : "ожидание"}
            {data.decision.reason !== "accepted" && data.decision.reason !== "idle" && (
              <span className="bad"> — команда {req?.action === "job" ? req.job_id : req?.action} отклонена: {REASON_TEXT[data.decision.reason] ?? data.decision.reason}</span>
            )}
            {data.decision.completed_job && <span className="good"> — задание {data.decision.completed_job} завершено</span>}
          </p>
          <div className="kv">
            <span>Известно</span><span>{data.known.jobs_known} заданий, {data.known.events_received.length} сообщений{data.known.events_received.length ? `: ${data.known.events_received.map((e) => e.id).join(", ")}` : ""}</span>
            <span>Заряд</span><span>{fmt.n(st.soc_pct as number, 1)}% ({fmt.n(st.energy_wh as number, 1)} из резерва {fmt.n(st.reserve_wh as number, 1)} Вт·ч) → {fmt.n(data.consequences.energy_after_wh as number, 1)} Вт·ч</span>
            <span>Температура</span><span>{fmt.n(st.temp_c as number, 1)} °C → {fmt.n(data.consequences.temp_after_c as number, 1)} °C</span>
            <span>Калибровка</span><span>возраст {String(st.calibration_age_steps)} из {String(st.calibration_valid_steps)} шагов</span>
            <span>Связь</span><span>Земля: {st.downlink_contact ? "есть" : "нет"} · ретрансляция: {st.relay_contact ? "есть" : "нет"} · солнце {fmt.n(st.solar_w as number)} Вт</span>
            <span>Каналы Земли</span><span>заняты другими: {(st.downlinks_used_by_others as string[]).join(", ") || "нет"}</span>
            <span>Доступен</span><span>{st.available ? "да" : "нет"}</span>
          </div>
          <h4>Варианты на этом шаге ({data.known.open_jobs_for_satellite} открытых заданий для аппарата)</h4>
          <Table dense
            head={["Действие", "Приоритет", "Срок", "Осталось", "$", "Допустимо", "Причина"]}
            rows={data.options.map((o) => [
              o.action === "calibrate" ? "калибровка" : `${o.job_id} (${o.kind})`,
              o.priority ?? "", o.deadline_step ?? "", o.remaining_steps ?? "", o.value_usd ?? "",
              o.admissible ? <span className="good">да</span> : <span className="bad">нет</span>,
              o.reason.startsWith("taken_by_") ? `выполняет ${o.reason.slice(9)}` : REASON_TEXT[o.reason] ?? o.reason,
            ])} />
          <p className="muted">Недопустимость варианта объясняет только этот шаг и не доказывает, что задание нельзя было выполнить к сроку.</p>
        </>
      )}
    </>
  );
  if (onClose) return <Modal title={title} onClose={onClose}>{body}</Modal>;
  return <Card title={title}>{body}</Card>;
}
