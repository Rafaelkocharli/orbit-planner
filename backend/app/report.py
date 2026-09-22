"""Human-readable shift report (HTML). The machine-readable counterpart is session.result()."""
from __future__ import annotations

import datetime as dt
from html import escape

from .analysis import analytics
from .runs import Run

GOAL_TEXT = {"priority": "Приоритетное обслуживание", "revenue": "Коммерческая отдача"}
SUMMARY_TEXT = {
    "steps_executed": "Выполнено шагов", "jobs_total": "Всего заданий", "jobs_completed": "Завершено",
    "jobs_due": "Срок наступил", "jobs_due_missed": "Просрочено",
    "critical_jobs_due": "Приоритет 3: срок наступил", "critical_jobs_completed_on_time": "Приоритет 3: в срок",
    "revenue_usd": "Выручка, $", "blocked_command_count": "Отклонённых команд",
    "below_reserve_satellite_steps": "Шагов ниже резерва", "brownout_satellite_steps": "Эпизодов нехватки питания",
    "critical_soc_satellite_steps": "Шагов ниже критического заряда", "minimum_soc_pct": "Минимальный заряд, %",
    "work_steps_in_missed_jobs": "Работа в просроченных заданиях, шагов",
}


def _table(headers, rows) -> str:
    head = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape('—' if c is None else str(c))}</td>" for c in r) + "</tr>"
                   for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f}%"


def _step_time(step: int, step_s: int) -> str:
    t = step * step_s
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}"


def render(run: Run) -> str:
    a = analytics(run.session)
    s, md = a["summary"], run.session.run_metadata
    step_s = run.session.env.s["time"]["step_s"]
    scen = run.session.initial_scenario["meta"]
    parts = [
        f"<h1>Отчёт по смене: {escape(scen['title'])}</h1>",
        f"<p>Запуск <code>{run.id}</code> · сценарий <code>{escape(scen['id'])}</code> · "
        f"цель: {GOAL_TEXT[run.goal]} · алгоритм {escape(md['algorithm'])} {escape(md['version'])} · "
        f"шаг {run.step} из {run.steps} ({_step_time(run.step, step_s)}) · "
        f"сформирован {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}</p>",
        "<h2>Итоги</h2>",
        _table(["Показатель", "Значение"], [[SUMMARY_TEXT.get(k, k), v] for k, v in s.items()
                                             if not isinstance(v, dict)]),
        f"<p>Доля заданий приоритета 3 в срок: {_pct(a['critical_rate'])}. "
        f"Средняя загрузка аппаратов: {_pct(a['utilization']['fleet_utilization'])}.</p>",
        "<h2>По приоритетам</h2>",
        _table(["Приоритет", "Всего", "Срок наступил", "В срок", "Доля", "Выручка, $", "Потеряно, $"],
               [[p["priority"], p["total"], p["due"], p["completed_on_time_of_due"],
                 _pct(p["rate"]), p["revenue_usd"], p["value_lost_usd"]]
                for p in a["by_priority"]]),
        "<h2>Причины невыполнения</h2>",
        _table(["Причина", "Заданий", "Потеряно, $", "Устранимо"],
               [[c["text"], c["jobs"], c["value_usd"], "да" if c["avoidable"] else "нет"]
                for c in a["missed"]["by_cause"]]) if a["missed"]["count"] else "<p>Просроченных заданий нет.</p>",
    ]
    critical_missed = [j for j in a["missed"]["jobs"] if j["priority"] == 3]
    if critical_missed:
        parts += ["<h3>Просроченные задания приоритета 3</h3>",
                  _table(["Задание", "Тип", "Окно", "Сделано", "Причина", "Обоснование"],
                         [[j["id"], j["kind"], f"{j['window'][0]}–{j['window'][1]}",
                           f"{j['work_done']}/{j['work_steps']}", j["text"],
                           (j.get("proof") or {}).get("statement", "")] for j in critical_missed])]
    parts += ["<h2>Полученные сообщения</h2>",
              _table(["Шаг", "Время", "ID", "Тип"],
                     [[e["at_step"], _step_time(e["at_step"], step_s), e["id"], e["type"]]
                      for e in run.session.events]) if run.session.events else "<p>Нет.</p>"]
    if run.goal_history:
        parts += ["<h2>Смена цели</h2>",
                  _table(["Шаг", "Новая цель"], [[g["step"], GOAL_TEXT[g["goal"]]] for g in run.goal_history])]
    deficits = a["deficit_periods"]
    parts += ["<h2>Периоды дефицита энергии</h2>",
              _table(["Аппарат", "Шаги", "Мин. заряд, %", "Критический", "Нехватка питания"],
                     [[d["satellite_id"], f"{d['start_step']}–{d['end_step']}", d["min_soc_pct"],
                       "да" if d["critical"] else "", "да" if d["brownout"] else ""] for d in deficits[:100]])
              if deficits else "<p>Не было.</p>"]
    blocked = a["blocked_commands"]
    parts += ["<h2>Отклонённые команды</h2>",
              _table(["Причина", "Количество"], sorted(blocked["by_reason"].items())) if blocked["count"]
              else "<p>Не было.</p>"]
    util = a["utilization"]["satellites"]
    parts += ["<h2>Загрузка аппаратов</h2>",
              _table(["Аппарат", "Задания", "Калибровка", "Ожидание", "Отклонено", "Недоступен", "Загрузка"],
                     [[sid, u["job_steps"], u["calibrate_steps"], u["idle_steps"], u["blocked_commands"],
                       u["unavailable_steps"], _pct(u["utilization"])]
                      for sid, u in sorted(util.items())])]
    style = ("body{font-family:system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1b1b1b}"
             "table{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:14px}"
             "td,th{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left}th{background:#f4f4f4}"
             "code{background:#f4f4f4;padding:0 4px}")
    return (f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Отчёт {run.id}</title>"
            f"<style>{style}</style></head><body>{''.join(parts)}</body></html>")
