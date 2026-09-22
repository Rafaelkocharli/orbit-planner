import { useEffect, useMemo, useState } from "react";
import { api, Dict, Job, RunState } from "../api";
import { CAUSE_TEXT, Card, fmt, Modal, Table } from "./ui";

const STATUS_TEXT: Record<string, string> = {
  completed: "выполнено", missed: "просрочено", open: "в работе", pending: "ожидает окна", infeasible: "невыполнимо",
};
const PAGE = 100;

export default function Jobs({ run }: { run: RunState }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [why, setWhy] = useState<Dict | null>(null);

  useEffect(() => {
    api.jobs(run.run_id).then(setJobs).catch(() => setJobs([]));
  }, [run.run_id, run.step, run.events.length]);

  const filtered = useMemo(() => jobs.filter((j) =>
    (!status || j.status === status) && (!priority || j.priority === Number(priority)) && (!kind || j.kind === kind)
    && (!search || j.id.toLowerCase().includes(search.toLowerCase()) || j.eligible_satellites.some((s) => s.toLowerCase() === search.toLowerCase()))),
  [jobs, status, priority, kind, search]);
  const counts = useMemo(() => jobs.reduce<Record<string, number>>((c, j) => ({ ...c, [j.status]: (c[j.status] ?? 0) + 1 }), {}), [jobs]);
  useEffect(() => setPage(0), [status, priority, kind, search]);

  return (
    <div className="split wide-left">
      <Card title={`Задания (${filtered.length} из ${jobs.length})`}>
        <div className="row filters">
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">все статусы</option>
            {Object.entries(STATUS_TEXT).map(([k, v]) => <option key={k} value={k}>{v} ({counts[k] ?? 0})</option>)}
          </select>
          <select value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">любой приоритет</option>
            {[3, 2, 1].map((p) => <option key={p} value={p}>приоритет {p}</option>)}
          </select>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">все типы</option>
            <option value="downlink">downlink</option>
            <option value="relay">relay</option>
          </select>
          <input placeholder="ID задания или аппарата" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <Table dense
          head={["ID", "Тип", "П", "$", "Окно", "Прогресс", "Исполнители", "Статус", ""]}
          rows={filtered.slice(page * PAGE, (page + 1) * PAGE).map((j) => [
            j.id, j.kind, j.priority, fmt.n(j.value_usd, 2),
            `${j.release_step}–${j.deadline_step}`,
            <span className="soc"><span className="bar" style={{ width: `${(100 * j.work_done) / j.work_steps}%` }} />{j.work_done}/{j.work_steps}</span>,
            j.executors.length ? j.executors.join(", ") : <span className="muted">{j.eligible_satellites.join(", ")}</span>,
            <span className={`status ${j.status}`}>{STATUS_TEXT[j.status] ?? j.status}</span>,
            j.status !== "completed" ? <button className="link" onClick={() => api.why(run.run_id, j.id).then(setWhy)}>почему?</button> : "",
          ])} />
        {filtered.length > PAGE && (
          <div className="row">
            <button disabled={page === 0} onClick={() => setPage(page - 1)}>←</button>
            <span className="muted">{page * PAGE + 1}–{Math.min(filtered.length, (page + 1) * PAGE)} из {filtered.length}</span>
            <button disabled={(page + 1) * PAGE >= filtered.length} onClick={() => setPage(page + 1)}>→</button>
          </div>
        )}
      </Card>
      {why && <WhyCard why={why} onClose={() => setWhy(null)} />}
    </div>
  );
}

export function WhyCard({ why, onClose }: { why: Dict; onClose: () => void }) {
  const job = why.job as Job;
  const cause = why.cause as { primary: string; text: string; breakdown: Record<string, number>; proof?: { statement: string }; competing_jobs?: { id: string; priority: number; value_usd: number; steps: number }[] } | undefined;
  const proof = (why.proof as { statement: string } | undefined) ?? cause?.proof;
  return (
    <Modal title={`Задание ${job.id}`} onClose={onClose}>
      <p>{job.kind}, приоритет {job.priority}, ${job.value_usd}, окно {job.release_step}–{job.deadline_step}, сделано {String(why.work_done)} из {job.work_steps}.</p>
      <p><strong>Статус:</strong> {STATUS_TEXT[String(why.status)] ?? String(why.status)}</p>
      {cause && <p><strong>Причина:</strong> {CAUSE_TEXT[cause.primary] ?? cause.primary} — {cause.text}</p>}
      {proof && <div className="verdict neutral"><strong>Обоснование невыполнимости</strong><div>{proof.statement}</div></div>}
      {cause && Object.keys(cause.breakdown).length > 0 && (
        <Table dense head={["Что было в шаги со связью", "Раз"]}
          rows={Object.entries(cause.breakdown).map(([k, v]) => [k === "worked" ? "работа над заданием" : CAUSE_TEXT[k] ?? k, v])} />
      )}
      {cause?.competing_jobs && (
        <p className="muted">Конкуренты: {cause.competing_jobs.map((c) => `${c.id} (п${c.priority}, $${c.value_usd}, ${c.steps} шаг.)`).join(", ")}</p>
      )}
      {why.status === "open" && <p className="muted">Задание ещё можно выполнить: доступных шагов со связью — {String(why.usable_steps_left)}.</p>}
    </Modal>
  );
}
