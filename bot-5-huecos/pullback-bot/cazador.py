#!/usr/bin/env python3
"""
CAZADOR · "acciones en juego" + ruptura del rango de apertura (bolsa de EE. UU.)

Cada día, a las 15:35 (hora de Madrid; 9:35 en Nueva York):
  1. Busca entre ~570 acciones de EE. UU. las que están "en juego": abren con un hueco al alza de
     +2 % o más, con la primera vela de 5 minutos alcista y un volumen anormal frente a su media.
     (Casi siempre hay una noticia detrás: resultados, un contrato, una mejora de recomendación…)
  2. Vigila las 20 con más volumen relativo. Si una rompe el máximo de sus primeros 5 minutos, compra.
  3. Stop muy ajustado: 0,1 × su rango medio diario (ATR). Si falla, sale rápido con poca pérdida.
  4. Las que funcionan se dejan correr hasta el final de la sesión (21:55 en Madrid) y se venden.

Solo compras con tu dinero: sin apalancamiento, sin cortos, sin deuda.
4 huecos -> con 10 € son 2,5 € por operación.

Comandos:
  python cazador.py hunt      sesión completa (la lanza GitHub Actions)
  python cazador.py scan      solo muestra qué acciones están hoy "en juego" (no compra)
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from bot import T212Error, client_from_env, log, notify, place_with_precision, wait_fill

NY = ZoneInfo("America/New_York")

SLOTS = int(os.getenv("CAZADOR_SLOTS", "4"))
GAP_MIN = 0.02          # hueco mínimo de apertura
STOP_ATR = 0.10         # stop = entrada - 0,1 x ATR(14)
TOP = 20                # cuántas "en juego" se vigilan
MIN_PRICE, MIN_ATR, MIN_AVGVOL = 5.0, 0.5, 1_000_000
OR_END = dt.time(9, 35)          # fin del rango de apertura (5 min)
ENTRY_UNTIL = dt.time(11, 30)    # no entrar más tarde de esto (2 h tras la apertura)
CLOSE_ALL = dt.time(15, 55)      # vender todo antes del cierre
MIN_ORDER_EUR = 1.0

UNIVERSE = [
    'MMM', 'AOS', 'ABT', 'ABBV', 'ACN', 'ADBE', 'AMD', 'AES', 'AFL', 'A', 'APD', 'ABNB', 'AKAM', 'ALB',
    'ARE', 'ALGN', 'ALLE', 'LNT', 'ALL', 'GOOGL', 'GOOG', 'MO', 'AMZN', 'AMCR', 'AEE', 'AEP', 'AXP', 'AIG',
    'AMT', 'AWK', 'AMP', 'AME', 'AMGN', 'APH', 'ADI', 'AON', 'APA', 'APO', 'AAPL', 'AMAT', 'APP', 'APTV',
    'ACGL', 'ADM', 'ARES', 'ANET', 'AJG', 'AIZ', 'T', 'ATO', 'ADSK', 'ADP', 'AZO', 'AVY', 'AXON', 'BKR',
    'BALL', 'BAC', 'BAX', 'BDX', 'BRK-B', 'BBY', 'TECH', 'BIIB', 'BLK', 'BX', 'XYZ', 'BE', 'BNY', 'BA',
    'BKNG', 'BSX', 'BMY', 'AVGO', 'BR', 'BRO', 'BF-B', 'BG', 'BXP', 'CHRW', 'CDNS', 'CPT', 'COF', 'CAH',
    'CCL', 'CARR', 'CVNA', 'CASY', 'CAT', 'CBOE', 'CBRE', 'CDW', 'COR', 'CNC', 'CNP', 'CF', 'CRL', 'SCHW',
    'CHTR', 'CVX', 'CMG', 'CB', 'CHD', 'CIEN', 'CI', 'CINF', 'CTAS', 'CSCO', 'C', 'CFG', 'CLX', 'CME',
    'CMS', 'KO', 'CTSH', 'COHR', 'COIN', 'CL', 'CMCSA', 'FIX', 'COP', 'ED', 'STZ', 'CEG', 'COO', 'CPRT',
    'GLW', 'CPAY', 'CTVA', 'CSGP', 'COST', 'CRH', 'CRWD', 'CCI', 'CSX', 'CMI', 'CVS', 'DHR', 'DRI', 'DDOG',
    'DVA', 'DECK', 'DE', 'DELL', 'DAL', 'DVN', 'DXCM', 'FANG', 'DLR', 'DG', 'DLTR', 'D', 'DPZ', 'DASH',
    'DOV', 'DOW', 'DHI', 'DTE', 'DUK', 'DD', 'ETN', 'EBAY', 'ECHO', 'ECL', 'EIX', 'EW', 'ELV', 'EME',
    'EMR', 'ETR', 'EOG', 'EQT', 'EFX', 'EQIX', 'ERIE', 'ESS', 'EL', 'EG', 'EVRG', 'P', 'ES', 'EXC',
    'EXE', 'EXPE', 'EXPD', 'EXR', 'XOM', 'FFIV', 'FDS', 'FICO', 'FAST', 'FRT', 'FDX', 'FDXF', 'FERG', 'FIS',
    'FITB', 'FSLR', 'FE', 'FISV', 'FLEX', 'F', 'FTNT', 'FTV', 'FOXA', 'FOX', 'BEN', 'FCX', 'GRMN', 'IT',
    'GE', 'GEHC', 'GEV', 'GEN', 'GNRC', 'GD', 'GIS', 'GM', 'GPC', 'GILD', 'GPN', 'GL', 'GDDY', 'GS',
    'HAL', 'HIG', 'HAS', 'HCA', 'DOC', 'HSIC', 'HSY', 'HPE', 'HLT', 'HD', 'HONA', 'HON', 'HRL', 'HST',
    'HWM', 'HPQ', 'HUBB', 'HUM', 'HBAN', 'HII', 'IBM', 'IEX', 'IDXX', 'ITW', 'ILMN', 'INCY', 'IR', 'PODD',
    'INTC', 'IBKR', 'ICE', 'IFF', 'IP', 'INTU', 'ISRG', 'IVZ', 'INVH', 'IQV', 'IRM', 'JBHT', 'JBL', 'JKHY',
    'J', 'JNJ', 'JCI', 'JPM', 'KVUE', 'KDP', 'KEY', 'KEYS', 'KMB', 'KIM', 'KMI', 'KKR', 'KLAC', 'KHC',
    'KR', 'LHX', 'LH', 'LRCX', 'LVS', 'LDOS', 'LEN', 'LII', 'LLY', 'LIN', 'LYV', 'LMT', 'L', 'LOW',
    'LULU', 'LITE', 'LYB', 'MTB', 'MPC', 'MAR', 'MRSH', 'MLM', 'MRVL', 'MAS', 'MA', 'MKC', 'MCD', 'MCK',
    'MDT', 'MRK', 'META', 'MET', 'MTD', 'MGM', 'MCHP', 'MU', 'MSFT', 'MAA', 'MRNA', 'MDLZ', 'MPWR', 'MNST',
    'MCO', 'MS', 'MOS', 'MSI', 'MSCI', 'NDAQ', 'NTAP', 'NFLX', 'NEM', 'NWSA', 'NWS', 'NEE', 'NKE', 'NI',
    'NDSN', 'NSC', 'NTRS', 'NOC', 'NCLH', 'NRG', 'NUE', 'NVDA', 'NVR', 'NXPI', 'ORLY', 'OXY', 'ODFL', 'OMC',
    'ON', 'OKE', 'ORCL', 'OTIS', 'PCAR', 'PKG', 'PLTR', 'PANW', 'PSKY', 'PH', 'PAYX', 'PYPL', 'PNR', 'PEP',
    'PFE', 'PCG', 'PM', 'PSX', 'PNW', 'PNC', 'PPG', 'PPL', 'PFG', 'PG', 'PGR', 'PLD', 'PRU', 'PEG',
    'PTC', 'PSA', 'PHM', 'PWR', 'QCOM', 'DGX', 'Q', 'RL', 'RJF', 'RDDT', 'RTX', 'O', 'REG', 'REGN',
    'RF', 'RSG', 'RMD', 'RVTY', 'HOOD', 'ROK', 'ROL', 'ROP', 'ROST', 'RCL', 'SPGI', 'CRM', 'SNDK', 'SBAC',
    'SLB', 'STX', 'SRE', 'NOW', 'SHW', 'SPG', 'SWKS', 'SJM', 'SW', 'SNA', 'SOLV', 'SO', 'LUV', 'SWK',
    'SBUX', 'STT', 'STLD', 'STE', 'SYK', 'SMCI', 'SYF', 'SNPS', 'SYY', 'TMUS', 'TROW', 'TTWO', 'TPR', 'TRGP',
    'TGT', 'TEL', 'TDY', 'TER', 'TSLA', 'TXN', 'TPL', 'TXT', 'TMO', 'TJX', 'TKO', 'TSCO', 'TT', 'TDG',
    'TRV', 'TRMB', 'TFC', 'TYL', 'TSN', 'USB', 'UBER', 'UDR', 'ULTA', 'UNP', 'UAL', 'UPS', 'URI', 'UNH',
    'UHS', 'VLO', 'VEEV', 'VTR', 'VLTO', 'VRSN', 'VRSK', 'VZ', 'VRTX', 'VRT', 'VTRS', 'VICI', 'V', 'VST',
    'VMRK', 'VMC', 'WRB', 'GWW', 'WAB', 'WMT', 'DIS', 'WBD', 'WM', 'WAT', 'WEC', 'WFC', 'WELL', 'WST',
    'WDC', 'WY', 'WSM', 'WMB', 'WTW', 'WDAY', 'WYNN', 'XEL', 'XYL', 'YUM', 'ZBRA', 'ZBH', 'ZTS', 'MSTR',
    'SOFI', 'RIVN', 'LCID', 'AFRM', 'UPST', 'MARA', 'RIOT', 'ARM', 'SHOP', 'SNOW', 'NET', 'DKNG', 'RBLX', 'U',
    'PATH', 'AI', 'IONQ', 'RKLB', 'ASTS', 'NU', 'GME', 'AMC', 'CELH', 'SOUN', 'OKLO', 'HIMS', 'TEM', 'CAVA',
    'ROKU', 'PINS', 'SNAP', 'LYFT', 'TOST', 'DUOL', 'CHWY', 'W', 'ETSY', 'ZS', 'MDB', 'TEAM', 'OKTA', 'TWLO',
    'PLUG', 'RUN', 'CLSK', 'HUT', 'BITF', 'CIFR', 'WULF', 'IREN', 'QS', 'JOBY', 'ACHR', 'SPCE', 'NIO', 'XPEV',
    'LI', 'BABA', 'PDD', 'JD', 'BIDU', 'TSM', 'ASML', 'SQ', 'SE', 'MELI', 'CPNG', 'SPOT', 'ZM', 'DOCU',
]


# --------------------------------------------------------------------------- utilidades

def ny_now() -> dt.datetime:
    return dt.datetime.now(NY)


def sleep_until(t: dt.time):
    while ny_now().time() < t:
        time.sleep(min(30.0, max(1.0, (dt.datetime.combine(ny_now().date(), t, NY) - ny_now()).total_seconds())))


def us_tickers(instruments: list[dict]) -> dict[str, str]:
    """Símbolo -> ticker de Trading 212 (acciones de EE. UU. en USD)."""
    usd = [i for i in instruments if i.get("currencyCode") == "USD" and i.get("type") == "STOCK"
           and str(i.get("ticker", "")).endswith("_US_EQ")]
    by_key: dict[str, str] = {}
    for i in usd:
        base = i["ticker"][: -len("_US_EQ")]
        for k in {base, str(i.get("shortName") or "")}:
            if k:
                by_key.setdefault(k.upper(), i["ticker"])
    out = {}
    for s in UNIVERSE:
        for k in (s, s.replace("-", "."), s.replace("-", "_"), s.replace("-", "")):
            if k.upper() in by_key:
                out[s] = by_key[k.upper()]
                break
    return out


def _yf():
    import yfinance as yf
    return yf


def _field(df: pd.DataFrame, name: str, syms: list[str]) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        return df[name]
    return df[[name]].rename(columns={name: syms[0]})


def prep_stats(syms: list[str]) -> pd.DataFrame:
    """Por acción: cierre de ayer, ATR(14), volumen medio diario y volumen medio de los primeros 5 min."""
    yf = _yf()
    today = ny_now().date()
    d = yf.download(syms, period="40d", interval="1d", auto_adjust=False, progress=False, group_by="column", threads=True)
    H, L, C, V = (_field(d, k, syms) for k in ("High", "Low", "Close", "Volume"))
    idx = pd.to_datetime(C.index).date
    past = np.array([x < today for x in idx])
    H, L, C, V = H[past], L[past], C[past], V[past]
    pc = C.shift(1)
    tr = np.maximum(H - L, np.maximum((H - pc).abs(), (L - pc).abs()))
    stats = pd.DataFrame({
        "prev_close": C.iloc[-1],
        "atr": tr.tail(14).mean(),
        "avgvol": V.tail(14).mean(),
    })
    m = yf.download(syms, period="20d", interval="5m", auto_adjust=False, progress=False, group_by="column",
                    threads=True, prepost=False)
    mv = _field(m, "Volume", syms)
    mv.index = pd.to_datetime(mv.index).tz_convert(NY)
    first = mv[(mv.index.time == dt.time(9, 30)) & (mv.index.date < today)]
    stats["or_vol_avg"] = first.tail(14).mean()
    return stats


def first_bar_today(syms: list[str]) -> pd.DataFrame:
    """Vela de 9:30-9:35 de hoy: o, h, l, c, v por acción."""
    yf = _yf()
    m = yf.download(syms, period="1d", interval="5m", auto_adjust=False, progress=False, group_by="column",
                    threads=True, prepost=False)
    out = {}
    for k, col in (("o", "Open"), ("h", "High"), ("l", "Low"), ("c", "Close"), ("v", "Volume")):
        f = _field(m, col, syms)
        f.index = pd.to_datetime(f.index).tz_convert(NY)
        f = f[(f.index.date == ny_now().date()) & (f.index.time == dt.time(9, 30))]
        out[k] = f.iloc[0] if len(f) else pd.Series(dtype=float)
    return pd.DataFrame(out)


def select_in_play(bar: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """Filtra las acciones 'en juego' y las ordena por volumen relativo."""
    df = bar.join(stats, how="inner").dropna(subset=["o", "h", "c", "v", "prev_close", "atr", "or_vol_avg"])
    df["gap"] = df["o"] / df["prev_close"] - 1
    df["rvol"] = df["v"] / df["or_vol_avg"]
    ok = ((df["gap"] >= GAP_MIN) & (df["c"] > df["o"]) & (df["o"] > MIN_PRICE) & (df["atr"] > MIN_ATR)
          & (df["avgvol"] > MIN_AVGVOL) & (df["rvol"] >= 1.0))
    return df[ok].sort_values("rvol", ascending=False).head(TOP)


def live_highs(syms: list[str]) -> pd.Series:
    """Máximo de hoy desde las 9:35 y último precio (velas de 1 min, tiempo real en EE. UU.)."""
    yf = _yf()
    m = yf.download(syms, period="1d", interval="1m", auto_adjust=False, progress=False, group_by="column",
                    threads=True, prepost=False)
    h = _field(m, "High", syms)
    h.index = pd.to_datetime(h.index).tz_convert(NY)
    h = h[h.index.time >= OR_END]
    return h.max()


def eurusd() -> float:
    try:
        x = _yf().download("EURUSD=X", period="5d", interval="1d", progress=False)["Close"]
        return float(np.ravel(x.dropna().values)[-1])
    except Exception:
        return 1.10


def stop_for(entry: float, atr: float) -> float:
    return entry - STOP_ATR * atr


# --------------------------------------------------------------------------- posiciones del cazador

_BOT_TICKERS: dict[str, bool] = {}


def my_positions(api, tickers: dict[str, str]) -> list[dict]:
    """Posiciones de EE. UU. del universo abiertas por la API (el bot). El historial solo se
    consulta la primera vez que aparece cada posición (límite de 6 consultas/min)."""
    rev = {t: s for s, t in tickers.items()}
    pos = [p for p in api.positions() if p["instrument"]["ticker"] in rev and p.get("quantityAvailableForTrading", 0) > 0]
    unknown = [p["instrument"]["ticker"] for p in pos if p["instrument"]["ticker"] not in _BOT_TICKERS]
    if unknown:
        recent = api.history(limit=50).get("items", [])
        for t in unknown:
            buys = [h for h in recent if h["order"].get("ticker") == t and h["order"].get("side") == "BUY"
                    and h["order"].get("status") == "FILLED"]
            _BOT_TICKERS[t] = bool(buys) and buys[0]["order"].get("initiatedFrom") == "API"
    return [{**p, "sym": rev[p["instrument"]["ticker"]]} for p in pos if _BOT_TICKERS.get(p["instrument"]["ticker"])]


def sell(api, p: dict, tickers: dict[str, str], why: str, tag: str, dry: bool):
    s = p["sym"]
    _BOT_TICKERS.pop(tickers[s], None)
    pnl = p["walletImpact"]["unrealizedProfitLoss"]
    cost = p["walletImpact"]["totalCost"] or 1
    if dry:
        notify(f"VENDERÍA {s}{tag}", f"{why} · {pnl:+.2f} € ({pnl / cost * 100:+.1f} %)", "money_with_wings")
        return
    try:
        order, q = place_with_precision(api, tickers[s], float(p["quantityAvailableForTrading"]), sell=True)
    except Exception as e:
        notify(f"⚠️ No pude vender {s}", str(e)[:250], "warning", 5)
        return
    fill = wait_fill(api, tickers[s], order["id"], tries=2)
    res = fill["fill"].get("walletImpact", {}).get("realisedProfitLoss", pnl) if fill else pnl
    notify(f"VENDIDA {s} {res:+.2f} € ({res / cost * 100:+.1f} %){tag}", why, "money_with_wings", 4)


# --------------------------------------------------------------------------- sesión

def cmd_hunt(force: bool = False):
    start = time.time()
    now = ny_now()
    if now.weekday() >= 5 and not force:
        log("Fin de semana.")
        return
    t = now.time()
    early = dt.time(8, 50) <= t <= dt.time(11, 0)      # trabajo de la mañana (vigila y compra)
    late = dt.time(15, 0) <= t <= dt.time(15, 50)       # trabajo de la tarde (solo stops y cierre)
    if not (early or late or force):
        log(f"No es mi hora (NY {t:%H:%M}).")
        return
    deadline = start + 5 * 3600 + 40 * 60              # GitHub corta a las 6 h
    dry = os.getenv("DRY_RUN") == "1"
    api = client_from_env()
    tag = ("" if api.env == "live" else " [DEMO]") + (" [SIMULACIÓN]" if dry else "")
    tickers = us_tickers(api.instruments())
    syms = [s for s in UNIVERSE if s in tickers]
    log(f"{len(syms)} acciones de EE. UU. disponibles en Trading 212")
    stats = prep_stats(syms)

    watch = pd.DataFrame()
    entered: set[str] = set()
    stops: dict[str, float] = {}
    fx = eurusd()

    if (early or force) and not late:
        sleep_until(dt.time(9, 35, 20))
        bar = first_bar_today(syms)
        if bar.empty:
            notify("Hoy no abre la bolsa de EE. UU.", "El cazador descansa.", "zzz", 2)
            return
        watch = select_in_play(bar, stats)
        if watch.empty:
            notify(f"Hoy no hay acciones en juego{tag}", "Ninguna abre con +2 % y volumen fuerte. Sin operaciones.", "zzz", 3)
        else:
            lst = " · ".join(f"{s} +{r.gap * 100:.1f}% vol×{r.rvol:.1f}" for s, r in watch.head(8).iterrows())
            notify(f"🎯 {len(watch)} acciones en juego hoy{tag}", f"Vigilo: {lst}. Compro si rompen el máximo de 9:30-9:35.",
                   "dart", 3)

    last_scan = 0.0
    last_pulse = ny_now().hour
    while True:
        now = ny_now()
        if time.time() > deadline and now.time() < CLOSE_ALL:
            log("Límite de tiempo del trabajo; sigue el turno de la tarde.")
            return
        mine = my_positions(api, tickers)

        # A) cierre del día
        if now.time() >= CLOSE_ALL:
            total = 0.0
            for p in mine:
                total += p["walletImpact"]["unrealizedProfitLoss"]
                sell(api, p, tickers, "cierre del día", tag, dry)
            s = api.summary()
            notify(f"🏁 Fin de la caza · cartera {s['totalValue']:.2f} €{tag}",
                   f"Posiciones cerradas: {len(mine)} (≈{total:+.2f} €). Realizado total {s['investments']['realizedProfitLoss']:+.2f} €",
                   "checkered_flag", 4)
            return

        # B) stops (precio en tiempo real de Trading 212)
        for p in mine:
            sym = p["sym"]
            if sym not in stops:
                atr = float(stats["atr"].get(sym, np.nan))
                stops[sym] = stop_for(float(p["averagePricePaid"]), atr) if not math.isnan(atr) else float(p["averagePricePaid"]) * 0.97
            if float(p["currentPrice"]) <= stops[sym]:
                sell(api, p, tickers, f"stop {stops[sym]:.2f} $", tag, dry)

        # C) entradas: rupturas del rango de apertura
        held = {p["sym"] for p in mine}
        free = SLOTS - len(held)
        pending = [s for s in watch.index if s not in entered and s not in held]
        if free > 0 and pending and now.time() < ENTRY_UNTIL and time.time() - last_scan > 20:
            last_scan = time.time()
            try:
                highs = live_highs(pending)
            except Exception as e:
                log("datos 1 min:", e)
                highs = pd.Series(dtype=float)
            broke = [s for s in pending if s in highs and highs[s] > watch.loc[s, "h"]]
            if broke:
                summ = api.summary()
                cash = float(summ["cash"]["availableToTrade"])
                slot = float(summ["totalValue"]) / SLOTS
                for s in broke[:free]:
                    amount = min(slot, cash * 0.98)
                    if amount < MIN_ORDER_EUR:
                        break
                    ref = float(highs[s])
                    qty = amount * fx / ref * 0.98
                    entered.add(s)
                    if dry:
                        notify(f"COMPRARÍA {s}{tag}", f"≈{amount:.2f} € · rompe {watch.loc[s, 'h']:.2f} $", "shopping_cart")
                        continue
                    try:
                        order, q = place_with_precision(api, tickers[s], qty, sell=False)
                    except Exception as e:
                        notify(f"⚠️ No pude comprar {s}", str(e)[:250], "warning", 3)
                        continue
                    fill = wait_fill(api, tickers[s], order["id"], tries=2)
                    px = float(fill["fill"]["price"]) if fill else ref
                    stops[s] = stop_for(px, float(watch.loc[s, "atr"]))
                    cash -= amount
                    _BOT_TICKERS[tickers[s]] = True
                    notify(f"🚀 COMPRADA {s}{tag}",
                           f"{q} acc. a {px:.2f} $ (≈{amount:.2f} €) · hueco +{watch.loc[s, 'gap'] * 100:.1f}% · "
                           f"vol×{watch.loc[s, 'rvol']:.1f} · stop {stops[s]:.2f} $", "rocket", 4)

        # si ya no se puede entrar y no hay nada abierto, no hace falta seguir encendido
        if not mine and now.time() >= ENTRY_UNTIL and not late:
            notify(f"Caza de la mañana terminada{tag}", "Sin posiciones abiertas. Hasta mañana a las 15:35.", "zzz", 2)
            return

        # D) resumen cada hora
        if now.hour != last_pulse:
            last_pulse = now.hour
            s = api.summary()
            lines = [f"{p['sym']} {p['walletImpact']['unrealizedProfitLoss'] / (p['walletImpact']['totalCost'] or 1) * 100:+.1f}%"
                     for p in mine]
            notify(f"Cartera {s['totalValue']:.2f} €{tag}", " · ".join(lines) if lines else "Sin posiciones abiertas",
                   "bar_chart", 2)

        time.sleep(3)


def cmd_scan():
    api = client_from_env()
    tickers = us_tickers(api.instruments())
    syms = [s for s in UNIVERSE if s in tickers]
    stats = prep_stats(syms)
    bar = first_bar_today(syms)
    if bar.empty:
        print("Aún no hay vela de apertura de hoy.")
        return
    w = select_in_play(bar, stats)
    print(w[["o", "h", "gap", "rvol", "atr"]].round(3).to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["hunt", "scan"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        cmd_hunt(a.force) if a.command == "hunt" else cmd_scan()
    except T212Error as e:
        notify("⚠️ Error de Trading 212 (cazador)", str(e)[:300], "rotating_light", 4)
        raise
    except Exception as e:
        notify("⚠️ Error en el cazador", f"{type(e).__name__}: {str(e)[:300]}", "rotating_light", 4)
        raise


if __name__ == "__main__":
    main()
