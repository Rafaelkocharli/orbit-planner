import { useEffect, useState } from "react";
import { api, RunState, Series } from "../api";
import { Card, fmt, LineChart, Table } from "./ui";

export default function Satellites({ run }: { run: RunState }) {
  const ids = Object.keys(run.satellites).sort();
  const [sid, setSid] = useState(ids[0]);
  const [series, setSeries] = useState<Series | null>(null);

  useEffect(() => {
    api.series(run.run_id, sid).then(setSeries).catch(() => setSeries(null));
  }, [run.run_id, sid, run.step]);

  const reserve = series?.limits.reserve_soc_pct ?? 30;
  return (
    <div className="split">
      <Card title="Аппараты">
        <Table dense
          head={["КА", "Заряд", "T, °C", "Калибр. ещё", "Доступен"]}
          rows={ids.map((id) => {
            const s = run.satellites[id];
            return [
              <button className={id === sid ? "link active" : "link"} onClick={() => setSid(id)}>{id}</button>,
              <span className="soc"><span className={s.soc_pct < reserve ? "bar low" : "bar"} style={{ width: `${Math.min(100, s.soc_pct)}%` }} />{fmt.n(s.soc_pct, 1)}%</span>,
              fmt.n(s.temp_c, 1),
              s.calibration_left_steps === 0 ? <span className="bad">нужна</span> : `${s.calibration_left_steps} шаг.`,
              s.available ? "да" : <span className="bad">нет</span>,
            ];
          })} />
      </Card>
      <div>
        {series && (
          <>
            <Card title={`${sid}: заряд, %`}>
              <LineChart x={series.step} marker={run.step}
                lines={[{ values: series.soc_pct, color: "var(--c1)", label: "заряд" }]}
                limits={[{ y: series.limits.reserve_soc_pct, label: "резерв", color: "var(--warn)" },
                  { y: series.limits.critical_soc_pct, label: "критический", color: "var(--bad)" }]} />
            </Card>
            <Card title={`${sid}: температура, °C`}>
              <LineChart x={series.step} marker={run.step}
                lines={[{ values: series.temp_c, color: "var(--c2)", label: "температура" }]}
                limits={[{ y: series.limits.payload_min_c, label: "мин.", color: "var(--warn)" },
                  { y: series.limits.payload_max_c, label: "макс.", color: "var(--bad)" }]} />
            </Card>
            <Card title={`${sid}: возраст калибровки, шагов`}>
              <LineChart x={series.step} marker={run.step} height={140}
                lines={[{ values: series.calibration_age_steps, color: "var(--c3)", label: "возраст" }]}
                limits={[{ y: series.limits.calibration_valid_steps, label: "срок действия", color: "var(--bad)" }]} />
            </Card>
            <Card title={`${sid}: окна связи и действия`}>
              <Strip label="Земля" values={series.environment.downlink_available} cls="dl" />
              <Strip label="Ретрансляция" values={series.environment.relay_available} cls="rl" />
              <div className="strip">
                <span>Действие</span>
                <div>{series.action.map((a, i) => <i key={i} className={`act-${a}`} title={`шаг ${i}: ${series.job_id[i] ?? a}`} />)}</div>
              </div>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

function Strip({ label, values, cls }: { label: string; values: boolean[]; cls: string }) {
  return (
    <div className="strip">
      <span>{label}</span>
      <div>{values.map((v, i) => <i key={i} className={v ? cls : ""} title={`шаг ${i}`} />)}</div>
    </div>
  );
}
