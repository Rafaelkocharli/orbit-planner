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
import { Logo, Tabs } from "./components/ui";
import WhatIf from "./components/WhatIf";

type Tab = "overview" | "events" | "schedule" | "satellites" | "jobs" | "analysis" | "compare" | "whatif";
type Theme = "dark" | "light";

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

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem("orbit-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch { /* private mode */ }
  return "dark";
}

export default function App() {
  const [meta, setMeta] = useState<Meta>();
  const [run, setRun] = useState<RunState>();
  const [tab, setTab] = useState<Tab>("overview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("orbit-theme", theme); } catch { /* ignore */ }
  }, [theme]);

  const loadMeta = useCallback(() => api.meta().then(setMeta).catch((e) => setError(e.message)), []);
  useEffect(() => { loadMeta(); }, [loadMeta]);

  const act = useCallback((fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    fn().catch((e: Error) => setError(e.message)).finally(() => setBusy(false));
  }, []);

  const open = (r: RunState) => {
    setRun(r);
    setTab("overview");
    setError("");
    window.scrollTo({ top: 0 });
  };

  return (
    <main>
      <header className="top">
        <div className="brand">
          <Logo />
          <div className="brand-copy">
            <h1>Orbit Planner</h1>
            <span className="muted">Автономное управление спутниковой группировкой</span>
          </div>
        </div>
        <div className="top-spacer" />
        <div className="top-meta">
          {busy && <span className="spinner" aria-label="Выполняется" />}
          <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title="Переключить тему">
            {theme === "dark" ? "Светлая" : "Тёмная"}
          </button>
        </div>
      </header>

      {error && (
        <div className="error-box" role="alert">
          <span>{error}</span>
          <button className="link" onClick={() => setError("")}>×</button>
        </div>
      )}

      {!meta && !error && (
        <div className="boot">
          <div className="rings" aria-hidden="true"><span /><span /><span /></div>
          <p className="muted">Подключение к серверу планирования…</p>
        </div>
      )}

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
