"""
Estrategia "8 huecos · RSI(2) pullback" sobre ~80 grandes europeas en EUR.

- El saldo se divide en 8 huecos (con 10 € -> 1,25 € por operación).
- COMPRA acciones por encima de su media de 200 sesiones que han caído fuerte a corto plazo
  (RSI de 2 días, calculado con el precio de ese momento):
    · de 16:40 a 17:25  -> RSI(2) < 30   (la señal principal)
    · durante el día    -> RSI(2) < 5    (solo desplomes muy fuertes)
  Primero las más castigadas.
- VENDE en cualquier momento (se revisa cada 30 min con el precio en tiempo real de Trading 212)
  en cuanto el precio supera su media de 5 sesiones. También con -10 % o a los 10 días.

El mismo cálculo se usa en el backtest (barras de 1 hora) y en vivo.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    slots: int = 8
    rsi_len: int = 2
    rsi_entry: float = 30.0          # franja de la tarde
    rsi_entry_intraday: float = 5.0  # resto del día
    trend_len: int = 200
    exit_len: int = 5
    stop_loss: float = 0.10
    max_hold_days: int = 10
    cost_per_side: float = 0.0005    # spread + deslizamiento estimado por operación


AFTERNOON_START = dt.time(16, 40)


def threshold_for(t: dt.time, p: Params) -> float:
    return p.rsi_entry if t >= AFTERNOON_START else p.rsi_entry_intraday


# --------------------------------------------------------------------------- indicadores

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


def daily_state(daily: pd.DataFrame, p: Params) -> dict[str, pd.DataFrame]:
    """Estado de cada acción al empezar cada día (todo con cierres ANTERIORES a ese día).
    Con esto el RSI(2) y las medias se actualizan al instante con el precio de ese momento."""
    n = p.rsi_len
    delta = daily.diff()
    au = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return {
        "au": au.shift(1), "ad": ad.shift(1), "prev": daily.shift(1),
        "sum_trend": daily.rolling(p.trend_len - 1, min_periods=p.trend_len - 1).sum().shift(1),
        "sum_exit": daily.rolling(p.exit_len - 1, min_periods=p.exit_len - 1).sum().shift(1),
    }


def live_rsi(au, ad, prev, price, n: int):
    a = 1.0 / n
    delta = price - prev
    au2 = (1 - a) * au + a * np.clip(delta, 0, None)
    ad2 = (1 - a) * ad + a * np.clip(-delta, 0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(ad2 == 0, 100.0, 100.0 - 100.0 / (1.0 + au2 / ad2))


def entry_candidates(state_row: dict[str, np.ndarray], price: np.ndarray, threshold: float, p: Params):
    """Índices de columnas con señal de compra, ordenados de más a menos sobrevendida."""
    r = live_rsi(state_row["au"], state_row["ad"], state_row["prev"], price, p.rsi_len)
    sma = (state_row["sum_trend"] + price) / p.trend_len
    with np.errstate(invalid="ignore"):
        ok = (price > sma) & (r < threshold) & ~np.isnan(price) & ~np.isnan(sma) & ~np.isnan(r)
    idx = np.where(ok)[0]
    return sorted(((float(r[k]), int(k)) for k in idx))


def exit_reason(sum_exit_prior: float, price: float, entry_price: float, days_held: int,
                is_afternoon: bool, p: Params) -> str | None:
    """Motivo de venta o None. sum_exit_prior = suma de los últimos (exit_len-1) cierres anteriores.
    days_held = sesiones desde la compra (0 = hoy)."""
    if price is None or np.isnan(price):
        return None
    if price <= entry_price * (1.0 - p.stop_loss):
        return f"stop -{p.stop_loss:.0%}"
    if days_held == 0:
        return None  # nunca vender el mismo día de la compra
    if not np.isnan(sum_exit_prior) and price > (sum_exit_prior + price) / p.exit_len:
        return f"rebote (> media {p.exit_len} días)"
    if days_held >= p.max_hold_days and is_afternoon:
        return f"tiempo ({days_held} sesiones)"
    return None


# --------------------------------------------------------------------------- backtest (barras 1 h)

def backtest_hourly(hourly: pd.DataFrame, p: Params = Params()):
    """hourly: cierres de barras de 1 h (índice en hora de Madrid, columnas = acciones).
    La barra que empieza a las 16:00 (cierra a las 17:00) cuenta como franja de la tarde."""
    hourly = hourly[hourly.index.hour.isin(range(9, 18))].dropna(how="all")
    D = pd.Index(hourly.index.date)
    daily = hourly.groupby(D).last()
    dates = list(daily.index)
    di = {d: i for i, d in enumerate(dates)}
    st = {k: v.values for k, v in daily_state(daily, p).items()}
    P = hourly.values
    cols = list(hourly.columns)
    cash = [1.0 / p.slots] * p.slots
    pos = [None] * p.slots   # (col, entry_price, entry_day_idx, qty)
    trades, eqs = [], {}
    for ti, t in enumerate(hourly.index):
        i = di[t.date()]
        if i < p.trend_len + 5:
            continue
        px = P[ti]
        afternoon = t.hour == 16
        for s in range(p.slots):
            if pos[s] is None:
                continue
            k, ep, ei, q = pos[s]
            why = exit_reason(st["sum_exit"][i, k], px[k], ep, i - ei, afternoon, p)
            if why:
                cash[s] = q * px[k] * (1 - p.cost_per_side)
                trades.append((dates[ei], t, cols[k], px[k] * (1 - p.cost_per_side) / (ep * (1 + p.cost_per_side)) - 1,
                               why, i - ei))
                pos[s] = None
        if t.hour <= 16 and any(x is None for x in pos):
            thr = p.rsi_entry if afternoon else p.rsi_entry_intraday
            row = {k: v[i] for k, v in st.items()}
            held = {x[0] for x in pos if x}
            cands = [c for c in entry_candidates(row, px, thr, p) if c[1] not in held]
            for s in range(p.slots):
                if pos[s] is None and cands:
                    _, k = cands.pop(0)
                    pos[s] = (k, px[k], i, cash[s] / (px[k] * (1 + p.cost_per_side)))
                    cash[s] = 0.0
        if t.hour == 17:
            eqs[pd.Timestamp(t.date())] = sum(cash) + sum(
                x[3] * (px[x[0]] if not np.isnan(px[x[0]]) else x[1]) for x in pos if x)
    eq = pd.Series(eqs, dtype=float)
    tr = pd.DataFrame(trades, columns=["entrada", "salida", "accion", "ret", "motivo", "dias"])
    b = daily.copy()
    b.index = pd.to_datetime(b.index)
    b = b.loc[eq.index]
    bench = (1 + b.pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod()
    return eq, tr, bench


def stats(eq: pd.Series, tr: pd.DataFrame, bench: pd.Series) -> dict:
    h = len(eq) // 2
    return {
        "dias": len(eq),
        "10€ se convierten en": round(10 * eq.iloc[-1] / eq.iloc[0], 2),
        "comprar y mantener todo el universo": round(10 * bench.iloc[-1] / bench.iloc[0], 2),
        "caida maxima %": round(float((eq / eq.cummax() - 1).min() * 100), 1),
        "operaciones": len(tr),
        "operaciones por dia": round(len(tr) / max(len(eq), 1), 2),
        "acierto %": round(float((tr["ret"] > 0).mean() * 100), 1) if len(tr) else None,
        "ganancia media por operacion %": round(float(tr["ret"].mean() * 100), 2) if len(tr) else None,
        "1a mitad bot vs mercado": f"x{eq.iloc[h] / eq.iloc[0]:.3f} vs x{bench.iloc[h] / bench.iloc[0]:.3f}",
        "2a mitad bot vs mercado": f"x{eq.iloc[-1] / eq.iloc[h]:.3f} vs x{bench.iloc[-1] / bench.iloc[h]:.3f}",
    }
