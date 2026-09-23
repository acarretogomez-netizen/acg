#!/usr/bin/env python3
"""
Bot "5 huecos · RSI(2) pullback" para Trading 212 (cuenta Invest en EUR).

Se ejecuta cada 30 minutos durante la sesión europea (lo lanza GitHub Actions):
  - revisa las posiciones con el precio en tiempo real de Trading 212 y vende las que ya han rebotado
  - entre las 16:40 y las 17:25 compra las acciones más sobrevendidas para llenar los huecos libres
  - cada hora te manda un resumen de la cartera al móvil

Comandos:
  python bot.py run [--force]   una pasada (--force ignora el horario, para probar)
  python bot.py backtest        backtest con datos reales de 1 h de los últimos ~2 años
  python bot.py resolve         comprueba que las acciones existen en tu Trading 212
  python bot.py status          te manda el estado de la cuenta

Variables de entorno:
  T212_API_KEY, T212_API_SECRET   credenciales de la API de Trading 212
  T212_ENV       live | demo      (por defecto live)
  NTFY_TOPIC     nombre secreto de tu canal de ntfy.sh
  DRY_RUN        1 = calcula y avisa pero NO manda órdenes
  NOTIFY_PULSE   1 = resumen cada hora (por defecto 1)
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import math
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from strategy import Params, backtest_hourly, exit_reason, rank_entries, stats

TZ = ZoneInfo("Europe/Madrid")
P = Params()

UNIVERSE = [
    ("SAP.DE", "DE0007164600", "SAP"),
    ("SIE.DE", "DE0007236101", "Siemens"),
    ("ALV.DE", "DE0008404005", "Allianz"),
    ("DTE.DE", "DE0005557508", "Deutsche Telekom"),
    ("MUV2.DE", "DE0008430026", "Munich Re"),
    ("ASML.AS", "NL0010273215", "ASML"),
    ("AD.AS", "NL0011794037", "Ahold Delhaize"),
    ("MC.PA", "FR0000121014", "LVMH"),
    ("OR.PA", "FR0000120321", "L'Oréal"),
    ("RMS.PA", "FR0000052292", "Hermès"),
    ("TTE.PA", "FR0000120271", "TotalEnergies"),
    ("SAN.PA", "FR0000120578", "Sanofi"),
    ("AI.PA", "FR0000120073", "Air Liquide"),
    ("SU.PA", "FR0000121972", "Schneider Electric"),
    ("BNP.PA", "FR0000131104", "BNP Paribas"),
    ("IBE.MC", "ES0144580Y14", "Iberdrola"),
    ("ITX.MC", "ES0148396007", "Inditex"),
    ("SAN.MC", "ES0113900J37", "Banco Santander"),
    ("BBVA.MC", "ES0113211835", "BBVA"),
    ("ENEL.MI", "IT0003128367", "Enel"),
    ("ISP.MI", "IT0000072618", "Intesa Sanpaolo"),
    ("UCG.MI", "IT0005239360", "UniCredit"),
]
NAMES = {y: n for y, _, n in UNIVERSE}

SESSION_START = dt.time(9, 5)
SESSION_END = dt.time(17, 25)
ENTRY_START = dt.time(16, 40)
MIN_ORDER_EUR = 1.0


def log(*a):
    print(dt.datetime.now(TZ).strftime("%H:%M:%S"), *a, flush=True)


# --------------------------------------------------------------------------- notificaciones

def notify(title: str, message: str, tags: str = "chart_with_upwards_trend", priority: int = 3):
    log(f"[aviso] {title} | {message}")
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return
    try:
        requests.post("https://ntfy.sh/", timeout=15, json={
            "topic": topic, "title": title, "message": message,
            "tags": [t for t in tags.split(",") if t], "priority": priority})
    except requests.RequestException as e:
        log("No se pudo enviar el aviso:", e)


# --------------------------------------------------------------------------- Trading 212

class T212Error(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class T212:
    def __init__(self, key: str, secret: str, env: str = "live"):
        if env not in ("demo", "live"):
            raise ValueError("T212_ENV debe ser 'demo' o 'live'")
        self.env = env
        self.base = f"https://{env}.trading212.com/api/v0"
        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Basic {token}", "Content-Type": "application/json"})

    def _req(self, method: str, path: str, retry_429: bool = True, **kw):
        for attempt in range(4):
            r = self.s.request(method, self.base + path, timeout=30, **kw)
            if r.status_code == 429 and retry_429 and attempt < 3:
                reset = r.headers.get("x-ratelimit-reset")
                wait = max(1.0, float(reset) - time.time()) if reset else 5.0
                log(f"Límite de peticiones, espero {wait:.0f}s")
                time.sleep(min(wait + 0.5, 65))
                continue
            if r.status_code >= 400:
                raise T212Error(r.status_code, r.text)
            return r.json() if r.content else None
        raise T212Error(429, "rate limit")

    def summary(self):
        return self._req("GET", "/equity/account/summary")

    def positions(self):
        return self._req("GET", "/equity/positions")

    def instruments(self):
        return self._req("GET", "/equity/metadata/instruments")

    def history(self, ticker: str, limit: int = 10):
        return self._req("GET", "/equity/history/orders", params={"ticker": ticker, "limit": limit})

    def market_order(self, ticker: str, quantity: float):
        # La API no es idempotente: nunca se reintenta a ciegas tras un timeout.
        return self._req("POST", "/equity/orders/market", retry_429=False,
                         json={"ticker": ticker, "quantity": quantity, "extendedHours": False})


def client_from_env() -> T212:
    key, secret = os.getenv("T212_API_KEY"), os.getenv("T212_API_SECRET")
    if not key or not secret:
        sys.exit("Faltan T212_API_KEY / T212_API_SECRET")
    return T212(key, secret, os.getenv("T212_ENV", "live"))


def resolve_tickers(instruments: list[dict]) -> dict[str, str]:
    """Símbolo de Yahoo -> ticker de Trading 212 (por ISIN, solo cotizaciones en EUR)."""
    by_isin: dict[str, list[dict]] = {}
    for ins in instruments:
        if ins.get("currencyCode") == "EUR" and ins.get("type") == "STOCK":
            by_isin.setdefault(ins.get("isin"), []).append(ins)
    out = {}
    for ysym, isin, _ in UNIVERSE:
        cands = sorted(by_isin.get(isin, []), key=lambda i: i.get("addedOn") or "")
        if cands:
            out[ysym] = cands[0]["ticker"]
    return out


# --------------------------------------------------------------------------- datos de mercado

def fetch_daily(symbols: list[str], period: str = "2y") -> pd.DataFrame:
    """Cierres diarios (la última fila es la sesión de hoy, con precio de hace ~15 min)."""
    import yfinance as yf

    df = yf.download(symbols, period=period, interval="1d", auto_adjust=True, progress=False,
                     group_by="column", threads=True)
    closes = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": symbols[0]})
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    return closes.dropna(how="all")


def fetch_hourly(symbols: list[str]) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(symbols, period="730d", interval="1h", auto_adjust=True, progress=False,
                     group_by="column", threads=True)
    closes = df["Close"]
    closes.index = pd.to_datetime(closes.index).tz_convert(TZ)
    return closes.dropna(how="all")


# --------------------------------------------------------------------------- órdenes

def round_down(x: float, decimals: int) -> float:
    f = 10 ** decimals
    return math.floor(x * f + 1e-9) / f


def place_with_precision(api: T212, ticker: str, qty: float, sell: bool):
    """Prueba precisiones de fracción hasta que Trading 212 acepte. Un 400 = orden rechazada
    (no ejecutada), así que reintentar con otra precisión es seguro."""
    last = None
    for dec in (4, 3, 2, 1, 0):
        q = round_down(qty, dec)
        if q <= 0:
            continue
        try:
            return api.market_order(ticker, -q if sell else q), q
        except T212Error as e:
            if e.status != 400:
                raise
            last = e
            log(f"Rechazada con {dec} decimales: {e.body[:200]}")
    raise last or RuntimeError("cantidad demasiado pequeña")


def wait_fill(api: T212, ticker: str, order_id: int, seconds: int = 45) -> dict | None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(10)
        try:
            for it in api.history(ticker, limit=5).get("items", []):
                if it.get("order", {}).get("id") == order_id and it.get("fill"):
                    return it
        except T212Error as e:
            log("historial:", e)
    return None


def bot_positions(api: T212, tickers: dict[str, str]) -> list[dict]:
    """Posiciones que abrió ESTE bot (última compra hecha vía API). Lo tuyo a mano no se toca."""
    rev = {t: y for y, t in tickers.items()}
    out = []
    for pos in api.positions():
        t = pos["instrument"]["ticker"]
        if t not in rev or pos.get("quantityAvailableForTrading", 0) <= 0:
            continue
        hist = api.history(t, limit=10).get("items", [])
        buys = [h for h in hist if h["order"].get("side") == "BUY" and h["order"].get("status") == "FILLED"]
        if not buys or buys[0]["order"].get("initiatedFrom") != "API":
            log(f"Ignoro {t}: no la compró el bot")
            continue
        entry = pd.Timestamp(buys[0]["fill"]["filledAt"]).tz_convert(TZ).tz_localize(None).normalize()
        out.append({**pos, "ysym": rev[t], "entry_date": entry})
    return out


def pct(pos: dict) -> float:
    cost = pos["walletImpact"]["totalCost"]
    return pos["walletImpact"]["unrealizedProfitLoss"] / cost * 100 if cost else 0.0


# --------------------------------------------------------------------------- una pasada

def in_session(now: dt.datetime) -> bool:
    return now.weekday() < 5 and SESSION_START <= now.time() <= SESSION_END


def cmd_run(force: bool = False):
    now = dt.datetime.now(TZ)
    if not force and not in_session(now):
        log(f"Fuera de horario de bolsa ({now:%a %H:%M}).")
        return
    entry_window = force or now.time() >= ENTRY_START
    dry = os.getenv("DRY_RUN") == "1"
    api = client_from_env()
    tag = ("" if api.env == "live" else " [DEMO]") + (" [SIMULACIÓN]" if dry else "")

    tickers = resolve_tickers(api.instruments())
    syms = [y for y, _, _ in UNIVERSE if y in tickers]
    daily = fetch_daily(syms)
    today = pd.Timestamp(now.date())
    fresh = [s for s in syms if s in daily.columns and daily[s].last_valid_index() == today]
    if len(fresh) < len(syms) / 2 and not force:
        log("Hoy no hay sesión (festivo).")
        return
    prior = daily[daily.index < today]
    live_px = {s: float(daily.loc[today, s]) for s in fresh}

    # 1) Ventas (precio en tiempo real de Trading 212)
    held = bot_positions(api, tickers)
    remaining = []
    for pos in held:
        s = pos["ysym"]
        px = float(pos["currentPrice"])
        days = int((daily.index > pos["entry_date"]).sum())
        reason = exit_reason(prior[s].values, px, float(pos["averagePricePaid"]), days,
                             entry_window and now.time() >= ENTRY_START, P)
        if not reason:
            remaining.append(pos)
            continue
        qty = float(pos["quantityAvailableForTrading"])
        pnl = pos["walletImpact"]["unrealizedProfitLoss"]
        if dry:
            notify(f"VENDERÍA {NAMES[s]}{tag}", f"{reason} · {pnl:+.2f} € ({pct(pos):+.1f} %)", "money_with_wings")
            continue
        try:
            order, q = place_with_precision(api, tickers[s], qty, sell=True)
        except Exception as e:
            notify(f"⚠️ No pude vender {NAMES[s]}", str(e)[:250], "warning", 4)
            remaining.append(pos)
            continue
        fill = wait_fill(api, tickers[s], order["id"])
        if fill:
            res = fill["fill"].get("walletImpact", {}).get("realisedProfitLoss", pnl)
            notify(f"VENDIDA {NAMES[s]} {res:+.2f} €{tag}",
                   f"{q} acc. a {fill['fill']['price']:.2f} € · {reason} · {days} día(s) dentro", "money_with_wings", 4)
        else:
            notify(f"Venta enviada: {NAMES[s]}{tag}", f"{q} acc. · {reason} (pendiente de confirmar)", "hourglass", 4)

    # 2) Compras (solo en la ventana de la tarde)
    if entry_window and len(remaining) < P.slots:
        summ = api.summary()
        cash = float(summ["cash"]["availableToTrade"])
        slot_eur = float(summ["totalValue"]) / P.slots
        exclude = {h["ysym"] for h in remaining}
        cands = rank_entries(daily, today, live_px, exclude, P)
        free = P.slots - len(remaining)
        for r, s in cands[:free]:
            amount = min(slot_eur, cash * 0.98)
            if amount < MIN_ORDER_EUR:
                break
            px = live_px[s]
            if dry:
                notify(f"COMPRARÍA {NAMES[s]}{tag}", f"≈{amount:.2f} € a ~{px:.2f} € · RSI2 {r:.0f}", "shopping_cart")
                cash -= amount
                continue
            try:
                order, q = place_with_precision(api, tickers[s], amount / px, sell=False)
            except Exception as e:
                notify(f"⚠️ No pude comprar {NAMES[s]}", str(e)[:250], "warning", 3)
                continue
            fill = wait_fill(api, tickers[s], order["id"])
            fpx = fill["fill"]["price"] if fill else px
            cash -= q * fpx
            notify(f"COMPRADA {NAMES[s]}{tag}",
                   f"{q} acc. a {fpx:.2f} € (≈{q * fpx:.2f} €) · RSI2 {r:.0f} · venderá al rebotar",
                   "shopping_cart", 4)

    # 3) Resumen horario
    if os.getenv("NOTIFY_PULSE", "1") == "1" and (now.minute < 30 or force):
        send_pulse(api, tickers, tag)


def send_pulse(api: T212, tickers: dict[str, str], tag: str = ""):
    s = api.summary()
    rev = {t: y for y, t in tickers.items()}
    mine = [p for p in api.positions() if p["instrument"]["ticker"] in rev]
    lines = [f"{NAMES[rev[p['instrument']['ticker']]]} {pct(p):+.1f} %" for p in mine]
    inv = s["investments"]
    notify(f"Cartera {s['totalValue']:.2f} €{tag}",
           (" · ".join(lines) if lines else "Sin posiciones") +
           f"\nLiquidez {s['cash']['availableToTrade']:.2f} € · Realizado total {inv['realizedProfitLoss']:+.2f} €",
           "bar_chart", 2)


# --------------------------------------------------------------------------- otros comandos

def cmd_backtest():
    syms = [y for y, _, _ in UNIVERSE]
    hourly = fetch_hourly(syms)
    lines = [f"# Backtest · {len(hourly.columns)} acciones · barras de 1 h", ""]
    for cost in (0.0005, 0.001):
        eq, tr, bench = backtest_hourly(hourly, Params(cost_per_side=cost))
        lines.append(f"## Coste por operación {cost:.2%}")
        lines += [f"- **{k}**: {v}" for k, v in stats(eq, tr, bench).items()]
        lines.append("")
    eq, tr, bench = backtest_hourly(hourly, P)
    if len(tr):
        lines += ["## Por motivo de venta", "", tr.groupby("motivo")["ret"].agg(["count", "mean"]).to_markdown(), "",
                  "## Últimas 20 operaciones", "", tr.tail(20).to_markdown(index=False)]
    text = "\n".join(lines)
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n")
    st = stats(eq, tr, bench)
    notify("Backtest terminado",
           f"10 € → {st['10€ se convierten en']} € (comprar y mantener: {st['comprar y mantener el universo']} €) · "
           f"{st['operaciones']} operaciones, {st['acierto %']} % ganadoras", "bar_chart")


def cmd_resolve():
    api = client_from_env()
    tickers = resolve_tickers(api.instruments())
    for y, isin, n in UNIVERSE:
        print(f"{n:22s} {y:9s} {isin}  ->  {tickers.get(y, 'NO ENCONTRADA')}")
    notify("Universo comprobado", f"{len(tickers)}/{len(UNIVERSE)} acciones encontradas en Trading 212", "white_check_mark")


def cmd_status():
    api = client_from_env()
    send_pulse(api, resolve_tickers(api.instruments()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "backtest", "resolve", "status"])
    ap.add_argument("--force", action="store_true", help="ignorar el horario")
    a = ap.parse_args()
    try:
        {"run": lambda: cmd_run(a.force), "backtest": cmd_backtest,
         "resolve": cmd_resolve, "status": cmd_status}[a.command]()
    except Exception as e:
        notify("⚠️ Error en el bot", f"{type(e).__name__}: {str(e)[:300]}", "rotating_light", 4)
        raise


if __name__ == "__main__":
    main()
