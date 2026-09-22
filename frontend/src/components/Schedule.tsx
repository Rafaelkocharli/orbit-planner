import { useEffect, useMemo, useState } from "react";
import { api, RunState, ScheduleItem } from "../api";
import Explain from "./Explain";
import { Card, fmt, REASON_TEXT } from "./ui";

const ROW = 16;
const LEFT = 44;

export default function Schedule({ run }: { run: RunState }) {
  const [lanes, setLanes] = useState<Record<string, ScheduleItem[]>>({});
  const [pick, setPick] = useState<{ step: number; sid: string } | null>(null);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    api.schedule(run.run_id).then((s) => setLanes(s.lanes)).catch(() => setLanes({}));
  }, [run.run_id, run.step]);

  const sids = useMemo(() => Object.keys(run.satellites).sort(), [run.satellites]);
  const W = Math.max(600, run.steps * 4 * zoom);
  const H = sids.length * ROW + 24;
  const sx = (s: number) => LEFT + (s / run.steps) * (W - LEFT - 8);

  return (
    <>
      <Card title="Расписание выполненных операций" actions={
        <span className="row">
          <span className="legend-inline"><i className="g-downlink" />передача на Землю <i className="g-relay" />ретрансляция
            <i className="g-calibrate" />калибровка <i className="g-blocked" />отклонено</span>
          <button onClick={() => setZoom(Math.max(1, zoom / 1.5))}>−</button>
          <button onClick={() => setZoom(Math.min(8, zoom * 1.5))}>+</button>
        </span>}>
        {run.step === 0 ? <p className="muted">Выполните хотя бы один шаг.</p> : (
          <div className="gantt-wrap">
            <svg width={W} height={H} className="gantt">
              {Array.from({ length: Math.floor(run.steps / 12) + 1 }, (_, h) => (
                <g key={h}>
                  <line x1={sx(h * 12)} x2={sx(h * 12)} y1={0} y2={H - 20} className="grid" />
                  {(zoom > 1.5 || h % 2 === 0) && <text x={sx(h * 12) + 2} y={H - 6} className="tick">{fmt.time(h * 12, run.step_s)}</text>}
                </g>
              ))}
              {sids.map((sid, row) => (
                <g key={sid} transform={`translate(0, ${row * ROW})`}>
                  <text x={4} y={12} className="tick">{sid}</text>
                  {(lanes[sid] ?? []).filter((it) => it.action !== "idle" || it.blocked).map((it, i) => (
                    <rect key={i} x={sx(it.start_step)} y={2} height={ROW - 4}
                      width={Math.max(1.5, sx(it.end_step) - sx(it.start_step) - 0.5)}
                      className={it.blocked ? "g-blocked" : it.action === "calibrate" ? "g-calibrate" : `g-${it.kind} p${it.priority}`}
                      onClick={() => setPick({ step: it.start_step, sid })}>
                      <title>{`${sid}, шаги ${it.start_step}–${it.end_step - 1}: ${it.action === "job" ? it.label : it.action}${it.priority ? `, приоритет ${it.priority}` : ""}${it.blocked ? ` — отклонено: ${REASON_TEXT[it.reason ?? ""] ?? it.reason}` : ""}`}</title>
                    </rect>
                  ))}
                </g>
              ))}
              <line x1={sx(run.step)} x2={sx(run.step)} y1={0} y2={H - 20} className="marker" />
            </svg>
          </div>
        )}
        <p className="muted">Нажмите на операцию, чтобы разобрать решение. Разобрать ожидание можно на вкладке «Анализ».</p>
      </Card>
      {pick && <Explain run={run} step={pick.step} sid={pick.sid} onClose={() => setPick(null)} />}
    </>
  );
}
