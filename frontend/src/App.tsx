import { useCallback, useEffect, useState } from "react";
import { api, Meta, RunState } from "./api";
import Analysis from "./components/Analysis";
import Compare from "./components/Compare";
import Events from "./components/Events";
import Jobs from "./components/Jobs";
import Overview from "./components/Overview";
import RunControl from "./components/RunControl";
import Satellites from "./components/Satellites";
import Schedule from "./components/Schedule";
import Setup from "./components/Setup";
import { Tabs } from "./components/ui";
import WhatIf from "./components/WhatIf";

type Tab = "overview" | "events" | "schedule" | "satellites" | "jobs" | "analysis" | "compare" | "whatif";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "events", label: "Сообщения" },
  { id: "schedule", label: "Расписание" },
  { id: "satellites", label: "Аппараты" },
  { id: "jobs", label: "Задания" },
  { id: "analysis", label: "Анализ потерь" },
  { id: "compare", label: "Сравнение" },
  { id: "whatif", label: "Что если" },
];

export default function App() {
  const [meta, setMeta] = useState<Meta>();
  const [run, setRun] = useState<RunState>();
  const [tab, setTab] = useState<Tab>("overview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadMeta = useCallback(() => api.meta().then(setMeta).catch((e) => setError(e.message)), []);
  useEffect(() => { loadMeta(); }, [loadMeta]);

  // Every user action goes through here: one busy flag, one place for errors.
  const act = useCallback((fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    fn().catch((e: Error) => setError(e.message)).finally(() => setBusy(false));
  }, []);

  const open = (r: RunState) => {
    setRun(r);
    setError("");
    window.scrollTo({ top: 0 });
  };

  return (
    <main>
      <header className="top">
        <h1>Orbit Planner</h1>
        <span className="muted">Автономное управление спутниковой группировкой</span>
        {busy && <span className="spinner" aria-label="Выполняется" />}
      </header>

      {error && (
        <div className="error-box" role="alert">
          <span>{error}</span>
          <button className="link" onClick={() => setError("")}>×</button>
        </div>
      )}

      {!meta && !error && <p className="muted">Подключение к серверу…</p>}

      {meta && !run && <Setup meta={meta} onMeta={loadMeta} onOpen={open} act={act} busy={busy} />}

      {meta && run && (
        <>
          <RunControl run={run} onRun={setRun} act={act} busy={busy} onClose={() => setRun(undefined)} />
          <Tabs tabs={TABS} value={tab} onChange={setTab} />
          {tab === "overview" && <Overview run={run} act={act} busy={busy} />}
          {tab === "events" && <Events run={run} onRun={setRun} act={act} busy={busy} />}
          {tab === "schedule" && <Schedule run={run} />}
          {tab === "satellites" && <Satellites run={run} />}
          {tab === "jobs" && <Jobs run={run} />}
          {tab === "analysis" && <Analysis run={run} />}
          {tab === "compare" && <Compare run={run} meta={meta} onOpen={open} act={act} busy={busy} />}
          {tab === "whatif" && <WhatIf run={run} act={act} busy={busy} />}
        </>
      )}
    </main>
  );
}
