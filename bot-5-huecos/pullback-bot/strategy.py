"""
Estrategia "5 huecos · RSI(2) pullback con salidas intradía".

- Universo: 22 grandes de la zona euro (en EUR -> sin comisión de cambio en Trading 212).
- El saldo se divide en 5 huecos (con 10 € -> 2 € por operación).
- COMPRA (a partir de las 16:40): acciones por encima de su media de 200 sesiones cuyo RSI(2),
  calculado con el precio de ese momento, está por debajo de 30. Primero las más sobrevendidas.
- VENDE en cualquier momento de la sesión (se revisa cada 30 min con el precio en tiempo real
  de Trading 212) en cuanto el precio supera su media de 5 sesiones. También con -10 % (stop) o
  a los 10 días.

El mismo código se usa en el backtest (barras de 1 hora) y en vivo.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    slots: int = 5
    rsi_len: int = 2
    rsi_entry: float = 30.0
    trend_len: int = 200
    exit_len: int = 5
    stop_loss: float = 0.10
    max_hold_days: int = 10
    cost_per_side: float = 0.0005   # spread + deslizamiento estimado por operación


def rsi(close: pd.Series, n: int) -> pd.Series:
    """RSI de Wilder."""
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    avg_up = up.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_down = down.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_up / avg_down.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(avg_down != 0.0, 100.0)


def rsi_last(values: np.ndarray, n: int) -> float:
    return float(rsi(pd.Series(values, dtype=float).ffill(), n).iloc[-1])


def entry_signal(prior_closes: np.ndarray, price: float, p: Params) -> float | None:
    """prior_closes: cierres diarios ANTERIORES a hoy (al menos trend_len). price: precio actual.
    Devuelve el RSI(2) si hay señal de compra, si no None."""
    x = np.append(np.asarray(prior_closes, dtype=float)[-(p.trend_len + 20):], price)
    if len(x) < p.trend_len + 1 or np.isnan(price) or np.isnan(x[-4:]).any():
        return None
    if np.isnan(x).sum() > 20:
        return None
    trend = np.nanmean(x[-p.trend_len:])
    r = rsi_last(x, p.rsi_len)
    if price > trend and r < p.rsi_entry:
        return r
    return None


def exit_reason(prior_closes: np.ndarray, price: float, entry_price: float, days_held: int,
                is_entry_window: bool, p: Params) -> str | None:
    """Motivo de venta o None. days_held = sesiones completas desde la compra (0 = hoy)."""
    if np.isnan(price):
        return None
    if price <= entry_price * (1.0 - p.stop_loss):
        return f"stop -{p.stop_loss:.0%}"
    if days_held == 0:
        return None  # nunca vender el mismo día de la compra
    last = np.asarray(prior_closes, dtype=float)[-(p.exit_len - 1):]
    sma = (np.nansum(last) + price) / (np.count_nonzero(~np.isnan(last)) + 1)
    if price > sma:
        return f"rebote (> media {p.exit_len} días)"
    if days_held >= p.max_hold_days and is_entry_window:
        return f"tiempo ({days_held} sesiones)"
    return None


def rank_entries(daily: pd.DataFrame, today, prices: dict[str, float], exclude: set[str], p: Params):
    """Lista [(rsi, símbolo)] de candidatos ordenados (más sobrevendido primero)."""
    prior = daily[daily.index < pd.Timestamp(today)]
    out = []
    for s, px in prices.items():
        if s in exclude or s not in prior.columns:
            continue
        r = entry_signal(prior[s].values, px, p)
        if r is not None:
            out.append((r, s))
    out.sort()
    return out


# --------------------------------------------------------------------------- backtest (barras 1h)

def backtest_hourly(hourly: pd.DataFrame, p: Params = Params(), entry_hour: int = 16):
    """hourly: precios de cierre de barras de 1 h (índice en hora de Madrid, columnas = símbolos).
    Compra con el cierre de la barra de las `entry_hour` (≈17:00), revisa ventas en cada barra."""
    hourly = hourly[hourly.index.hour.isin(range(9, 18))].dropna(how="all")
    D = pd.Index(hourly.index.date)
    daily = hourly.groupby(D).last()
    dates = list(daily.index)
    di = {d: i for i, d in enumerate(dates)}
    dv = daily.values
    cols = list(hourly.columns)
    P = hourly.values
    cash = [1.0 / p.slots] * p.slots
    pos = [None] * p.slots          # (col, entry_price, entry_day_idx, qty)
    trades, eqs = [], {}
    for t_i, t in enumerate(hourly.index):
        d = t.date()
        i = di[d]
        if i < p.trend_len + 2:
            continue
        prior = dv[:i]
        now = P[t_i]
        entry_bar = t.hour == entry_hour
        for s in range(p.slots):
            if pos[s] is None:
                continue
            k, ep, edi, q = pos[s]
            reason = exit_reason(prior[:, k], now[k], ep, i - edi, entry_bar, p)
            if reason:
                px = now[k]
                cash[s] = q * px * (1 - p.cost_per_side)
                trades.append((dates[edi], d, cols[k], px * (1 - p.cost_per_side) / (ep * (1 + p.cost_per_side)) - 1,
                               reason, i - edi))
                pos[s] = None
        if entry_bar:
            held = {x[0] for x in pos if x}
            cands = []
            for k in range(len(cols)):
                if k in held:
                    continue
                r = entry_signal(prior[:, k], now[k], p)
                if r is not None:
                    cands.append((r, k))
            cands.sort()
            for s in range(p.slots):
                if pos[s] is None and cands:
                    _, k = cands.pop(0)
                    ep = now[k]
                    pos[s] = (k, ep, i, cash[s] / (ep * (1 + p.cost_per_side)))
                    cash[s] = 0.0
        if t.hour == 17:
            eqs[d] = sum(cash) + sum(x[3] * (now[x[0]] if not np.isnan(now[x[0]]) else x[1]) for x in pos if x)
    eq = pd.Series(eqs, dtype=float)
    eq.index = pd.to_datetime(eq.index)
    tr = pd.DataFrame(trades, columns=["entrada", "salida", "accion", "ret", "motivo", "dias"])
    bench_daily = daily.loc[[x for x in daily.index if pd.Timestamp(x) in eq.index]]
    bench = (bench_daily / bench_daily.iloc[0]).mean(axis=1)
    bench.index = pd.to_datetime(bench.index)
    return eq, tr, bench


def stats(eq: pd.Series, tr: pd.DataFrame, bench: pd.Series) -> dict:
    h = len(eq) // 2
    return {
        "dias": len(eq),
        "10€ se convierten en": round(10 * eq.iloc[-1] / eq.iloc[0], 2),
        "comprar y mantener el universo": round(10 * bench.iloc[-1] / bench.iloc[0], 2),
        "caida maxima %": round(float((eq / eq.cummax() - 1).min() * 100), 1),
        "operaciones": len(tr),
        "operaciones por dia": round(len(tr) / max(len(eq), 1), 2),
        "acierto %": round(float((tr["ret"] > 0).mean() * 100), 1) if len(tr) else None,
        "ganancia media por operacion %": round(float(tr["ret"].mean() * 100), 2) if len(tr) else None,
        "1a mitad bot vs mercado": f"x{eq.iloc[h] / eq.iloc[0]:.3f} vs x{bench.iloc[h] / bench.iloc[0]:.3f}",
        "2a mitad bot vs mercado": f"x{eq.iloc[-1] / eq.iloc[h]:.3f} vs x{bench.iloc[-1] / bench.iloc[h]:.3f}",
    }
