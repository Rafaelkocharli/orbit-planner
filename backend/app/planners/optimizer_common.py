"""Shared pieces of the planners: availability, idle temperature forecast, energy lower bound."""
from __future__ import annotations

import math

UNITS_PER_WH = 100  # energy is tracked in integer 0.01 Wh units by both planners


def step_terms(k_coef: float, b0: float, powers) -> tuple[int, list[int]]:
    """Conservatively rounded energy change: floor for the base balance, ceil per action."""
    return math.floor(k_coef * b0), [math.ceil(k_coef * p) for p in powers]


def unavailable(env, sid: str, t: int) -> bool:
    return any(f["satellite_id"] == sid and f["start_step"] <= t < f["end_step"] for f in env.s["failures"])


def idle_temperature(env, sid: str, k: int, H: int) -> tuple[list[float], list[float]]:
    """Temperature and heater power per step if the satellite only idles.

    Idling is the coldest trajectory: any action adds load and raises the equilibrium,
    so heater use predicted here is an upper bound and charging is predicted conservatively.
    """
    v, m, e = env.sats[sid], env.s["model"], env.s["environment"][sid]
    dt = env.s["time"]["step_s"]
    decay = math.exp(-dt / m["thermal_tau_s"])
    temp = env.state[sid]["temp_c"]
    temps, heaters = [], []
    for t in range(k, k + H):
        h = v["heater_w"] if temp < m["heater_below_c"] + 0.5 else 0.0
        temps.append(temp)
        heaters.append(h)
        eq = e["thermal_target_c"][t] + m["thermal_gain_c_per_w"] * (v["base_w"] + h)
        temp = eq + (temp - eq) * decay
    return temps, heaters


class EnergyModel:
    """Lower bound of one satellite's charge over [k, end) for a set of planned actions.

    Same transition as the model with the pessimistic heater/charging forecast and the same
    integer rounding as the CP-SAT model, so a plan admitted here is feasible there and the
    real charge stays above this bound. `try_add` admits an action only if every planned
    action keeps the bound above the reserve at the start and end of its step.
    """

    def __init__(self, env, sid: str, k: int, end: int, margin_wh: float = 0.3):
        v, m = env.sats[sid], env.s["model"]
        dt = env.s["time"]["step_s"]
        U = UNITS_PER_WH
        self.k, self.n = k, end - k
        self.cap = int(math.floor(v["capacity_wh"] * U))
        self.reserve = int(math.ceil((v["capacity_wh"] * m["reserve_soc_pct"] / 100 + margin_wh) * U))
        self.k_pos = m["charge_efficiency"] * dt / 3600 * U
        self.k_neg = dt / 3600 / m["discharge_efficiency"] * U
        temps, heaters = idle_temperature(env, sid, k, self.n)
        solar = env.s["environment"][sid]["solar_w"]
        self.base_b = [solar[k + i] - v["base_w"] - heaters[i] for i in range(self.n)]
        self.charge = [m["charge_min_c"] + 0.5 <= temps[i] <= m["charge_max_c"] - 0.5 for i in range(self.n)]
        self.power = [0.0] * self.n
        self.E = [min(self.cap, int(math.floor(env.state[sid]["energy_wh"] * U)))]
        for i in range(self.n):
            self.E.append(self._next(i, self.E[i], 0.0))

    def _next(self, i: int, e: int, p: float) -> int:
        neg = math.floor(self.k_neg * self.base_b[i]) - (math.ceil(self.k_neg * p) if p else 0)
        if self.charge[i]:
            pos = math.floor(self.k_pos * self.base_b[i]) - (math.ceil(self.k_pos * p) if p else 0)
        else:
            pos = 0
        return min(self.cap, e + min(pos, neg))

    def try_add(self, t: int, p: float) -> bool:
        i = t - self.k
        if not 0 <= i < self.n or self.power[i]:
            return False
        self.power[i] = p
        new, cur = {}, self.E[i]
        for j in range(i, self.n):
            nxt = self._next(j, cur, self.power[j])
            if self.power[j] and (cur < self.reserve or nxt < self.reserve):
                self.power[i] = 0.0
                return False
            new[j + 1] = nxt
            if j > i and nxt == self.E[j + 1]:
                break  # trajectories merged at the capacity clamp
            cur = nxt
        for idx, val in new.items():
            self.E[idx] = val
        return True

    def remove(self, t: int, p: float) -> None:
        i = t - self.k
        self.power[i] = 0.0
        cur = self.E[i]
        for j in range(i, self.n):
            nxt = self._next(j, cur, self.power[j])
            if j > i and nxt == self.E[j + 1]:
                break
            self.E[j + 1] = nxt
            cur = nxt
