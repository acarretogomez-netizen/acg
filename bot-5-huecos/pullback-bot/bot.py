#!/usr/bin/env python3
"""
Bot "8 huecos · RSI(2) pullback" para Trading 212 (cuenta Invest en EUR), ~80 acciones europeas.

Se ejecuta cada 30 minutos durante la sesión europea (lo lanza GitHub Actions):
  - revisa las posiciones con el precio en tiempo real de Trading 212 y vende las que ya han rebotado
  - compra para llenar huecos libres: de 16:40 a 17:25 con RSI(2) < 30; antes, solo si hay desplomes (RSI(2) < 5)
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
import re
import sys
import time
import unicodedata
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from strategy import (AFTERNOON_START, Params, backtest_hourly, daily_state, entry_candidates, exit_reason,
                      stats, threshold_for)

TZ = ZoneInfo("Europe/Madrid")
P = Params()

# (símbolo de Yahoo, palabras del nombre en Trading 212, nombre corto, ISIN si lo sé seguro)
UNIVERSE = [
    # Alemania
    ("SAP.DE", "SAP", "SAP", "DE0007164600"),
    ("SIE.DE", "Siemens", "Siemens", "DE0007236101"),
    ("ALV.DE", "Allianz", "Allianz", "DE0008404005"),
    ("DTE.DE", "Telekom", "Deutsche Telekom", "DE0005557508"),
    ("MUV2.DE", "Munich|Münchener|Muenchener", "Munich Re", "DE0008430026"),
    ("BAS.DE", "BASF", "BASF", None),
    ("BAYN.DE", "Bayer", "Bayer", None),
    ("BMW.DE", "BMW|Bayerische Motoren", "BMW", None),
    ("MBG.DE", "Mercedes", "Mercedes-Benz", None),
    ("VOW3.DE", "Volkswagen", "Volkswagen", None),
    ("ADS.DE", "adidas", "adidas", None),
    ("DB1.DE", "Deutsche Börse|Deutsche Boerse", "Deutsche Börse", None),
    ("IFX.DE", "Infineon", "Infineon", None),
    ("DHL.DE", "DHL|Deutsche Post", "DHL", None),
    ("RHM.DE", "Rheinmetall", "Rheinmetall", None),
    ("ENR.DE", "Siemens Energy", "Siemens Energy", None),
    ("DBK.DE", "Deutsche Bank", "Deutsche Bank", None),
    ("EOAN.DE", "E.ON|EON", "E.ON", None),
    ("HNR1.DE", "Hannover", "Hannover Re", None),
    ("MTX.DE", "MTU", "MTU Aero", None),
    ("CBK.DE", "Commerzbank", "Commerzbank", None),
    # Francia
    ("MC.PA", "LVMH", "LVMH", "FR0000121014"),
    ("OR.PA", "L'Or|L’Or|Oreal", "L'Oréal", "FR0000120321"),
    ("RMS.PA", "Herm", "Hermès", "FR0000052292"),
    ("TTE.PA", "Total", "TotalEnergies", "FR0000120271"),
    ("SAN.PA", "Sanofi", "Sanofi", "FR0000120578"),
    ("AI.PA", "Air Liquide", "Air Liquide", "FR0000120073"),
    ("SU.PA", "Schneider", "Schneider Electric", "FR0000121972"),
    ("BNP.PA", "BNP", "BNP Paribas", "FR0000131104"),
    ("CS.PA", "AXA", "AXA", None),
    ("SAF.PA", "Safran", "Safran", None),
    ("AIR.PA", "Airbus", "Airbus", None),
    ("EL.PA", "Essilor", "EssilorLuxottica", None),
    ("KER.PA", "Kering", "Kering", None),
    ("DG.PA", "Vinci", "Vinci", None),
    ("BN.PA", "Danone", "Danone", None),
    ("RI.PA", "Pernod", "Pernod Ricard", None),
    ("SGO.PA", "Gobain|Saint-Gobain", "Saint-Gobain", None),
    ("GLE.PA", "Generale|Société Générale", "Société Générale", None),
    ("ACA.PA", "Agricole", "Crédit Agricole", None),
    ("CAP.PA", "Capgemini", "Capgemini", None),
    ("ORA.PA", "Orange", "Orange", None),
    ("ENGI.PA", "Engie", "Engie", None),
    ("HO.PA", "Thales", "Thales", None),
    ("LR.PA", "Legrand", "Legrand", None),
    ("STMPA.PA", "STMicro", "STMicroelectronics", None),
    # Países Bajos
    ("ASML.AS", "ASML", "ASML", "NL0010273215"),
    ("AD.AS", "Ahold|Koninklijke Ahold", "Ahold Delhaize", "NL0011794037"),
    ("INGA.AS", "ING", "ING", None),
    ("PRX.AS", "Prosus", "Prosus", None),
    ("WKL.AS", "Wolters", "Wolters Kluwer", None),
    ("HEIA.AS", "Heineken", "Heineken", None),
    ("ADYEN.AS", "Adyen", "Adyen", None),
    ("PHIA.AS", "Philips|Koninklijke Philips", "Philips", None),
    ("ASM.AS", "ASM International", "ASM International", None),
    ("ABN.AS", "ABN", "ABN AMRO", None),
    # España
    ("IBE.MC", "Iberdrola", "Iberdrola", "ES0144580Y14"),
    ("ITX.MC", "Inditex|Industria de Dise", "Inditex", "ES0148396007"),
    ("SAN.MC", "Santander", "Banco Santander", "ES0113900J37"),
    ("BBVA.MC", "BBVA|Bilbao", "BBVA", "ES0113211835"),
    ("CABK.MC", "CaixaBank", "CaixaBank", None),
    ("TEF.MC", "Telef", "Telefónica", None),
    ("REP.MC", "Repsol", "Repsol", None),
    ("AMS.MC", "Amadeus", "Amadeus", None),
    ("ACS.MC", "ACS|Actividades de Constr", "ACS", None),
    ("AENA.MC", "Aena", "Aena", None),
    ("SAB.MC", "Sabadell|Banco de Sabadell", "Banco Sabadell", None),
    ("CLNX.MC", "Cellnex", "Cellnex", None),
    # Italia
    ("ENEL.MI", "Enel", "Enel", "IT0003128367"),
    ("ISP.MI", "Intesa", "Intesa Sanpaolo", "IT0000072618"),
    ("UCG.MI", "UniCredit", "UniCredit", "IT0005239360"),
    ("ENI.MI", "Eni", "Eni", None),
    ("STLAM.MI", "Stellantis", "Stellantis", None),
    ("RACE.MI", "Ferrari", "Ferrari", None),
    ("G.MI", "Generali|Assicurazioni", "Generali", None),
    ("PRY.MI", "Prysmian", "Prysmian", None),
    ("MB.MI", "Mediobanca", "Mediobanca", None),
    ("LDO.MI", "Leonardo", "Leonardo", None),
    # Bélgica y Finlandia
    ("ABI.BR", "Anheuser|AB InBev", "AB InBev", None),
    ("KBC.BR", "KBC", "KBC", None),
    ("NDA-FI.HE", "Nordea", "Nordea", None),
    ("NOKIA.HE", "Nokia", "Nokia", None),
]
NAMES = {u[0]: u[2] for u in UNIVERSE}

SESSION_START = dt.time(9, 5)
SESSION_END = dt.time(17, 25)
FIRST_BUY = dt.time(9, 35)   # no comprar en los primeros minutos (precios de apertura muy movidos)
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

    def history(self, ticker: str | None = None, limit: int = 50):
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._req("GET", "/equity/history/orders", params=params)

    def market_order(self, ticker: str, quantity: float):
        # La API no es idempotente: nunca se reintenta a ciegas tras un timeout.
        return self._req("POST", "/equity/orders/market", retry_429=False,
                         json={"ticker": ticker, "quantity": quantity, "extendedHours": False})


def client_from_env() -> T212:
    key, secret = os.getenv("T212_API_KEY"), os.getenv("T212_API_SECRET")
    if not key or not secret:
        sys.exit("Faltan T212_API_KEY / T212_API_SECRET")
    return T212(key, secret, os.getenv("T212_ENV", "live"))


def _norm(x: str) -> str:
    x = unicodedata.normalize("NFKD", x or "")
    return "".join(ch for ch in x if not unicodedata.combining(ch)).lower().replace("’", "'")


def _base(ticker: str) -> str:
    """'SAPd_EQ' -> 'SAP', 'MUV2d_EQ' -> 'MUV2' (quita el sufijo de bolsa en minúsculas)."""
    t = ticker.split("_")[0]
    return re.sub(r"[a-z]+$", "", t)


def resolve_tickers(instruments: list[dict]) -> dict[str, str]:
    """Símbolo de Yahoo -> ticker de Trading 212. Solo acciones en EUR. Primero por ISIN; si no,
    por nombre + símbolo. Si hay dudas, la acción se descarta (mejor no operarla que operar otra)."""
    eur = [i for i in instruments if i.get("currencyCode") == "EUR" and i.get("type") == "STOCK"]
    by_isin: dict[str, list[dict]] = {}
    for ins in eur:
        by_isin.setdefault(ins.get("isin"), []).append(ins)
    out = {}
    for ysym, kw, _, isin in UNIVERSE:
        cands = by_isin.get(isin, []) if isin else []
        if not cands:
            ybase = re.sub(r"[^A-Z0-9]", "", ysym.split(".")[0].upper())
            pats = [(len(k), re.compile(r"(^|[^a-z0-9])" + re.escape(_norm(k)))) for k in kw.split("|")]
            hits = []
            for i in eur:
                lens = [n for n, p in pats if p.search(_norm(i.get("name", "")))]
                if lens:
                    same = re.sub(r"[^A-Z0-9]", "", _base(i["ticker"]).upper()) == ybase
                    hits.append((i, same, max(lens)))
            same = [i for i, ok, _ in hits if ok]
            # con varias coincidencias manda el símbolo; con una sola, vale si el nombre es distintivo
            if same:
                cands = same
            elif len(hits) == 1 and hits[0][2] >= 5:
                cands = [hits[0][0]]
        if cands:
            cands.sort(key=lambda i: i.get("addedOn") or "")
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


def wait_fill(api: T212, ticker: str, order_id: int, tries: int = 3) -> dict | None:
    for _ in range(tries):
        time.sleep(12)
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
    mine = [p for p in api.positions()
            if p["instrument"]["ticker"] in rev and p.get("quantityAvailableForTrading", 0) > 0]
    if not mine:
        return []
    recent = api.history(limit=50).get("items", [])  # una sola llamada para todas
    out = []
    for pos in mine:
        t = pos["instrument"]["ticker"]
        hist = [h for h in recent if h["order"].get("ticker") == t]
        if not hist:
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
    afternoon = now.time() >= AFTERNOON_START
    dry = os.getenv("DRY_RUN") == "1"
    api = client_from_env()
    tag = ("" if api.env == "live" else " [DEMO]") + (" [SIMULACIÓN]" if dry else "")

    tickers = resolve_tickers(api.instruments())
    syms = [u[0] for u in UNIVERSE if u[0] in tickers]
    daily = fetch_daily(syms)
    syms = [x for x in syms if x in daily.columns]
    daily = daily[syms]
    today = pd.Timestamp(now.date())
    if today not in daily.index or daily.loc[today].notna().sum() < len(syms) / 2:
        if not force:
            log("Hoy no hay sesión (festivo).")
            return
        daily.loc[today] = daily.ffill().iloc[-1]
    state = {k: v.loc[today].values for k, v in daily_state(daily, P).items()}
    col = {x: i for i, x in enumerate(syms)}
    live_px = daily.loc[today].values.astype(float)   # Yahoo, ~15 min de retraso

    # 1) Ventas, con el precio en tiempo real de Trading 212
    held = bot_positions(api, tickers)
    remaining = []
    for pos in held:
        s = pos["ysym"]
        px = float(pos["currentPrice"])
        days = int((daily.index > pos["entry_date"]).sum())
        reason = exit_reason(state["sum_exit"][col[s]], px, float(pos["averagePricePaid"]), days, afternoon, P)
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

    # 2) Compras: por la tarde con RSI(2) < 30; el resto del día solo desplomes (RSI(2) < 5)
    if (force or now.time() >= FIRST_BUY) and len(remaining) < P.slots:
        thr = threshold_for(now.time(), P)
        held_syms = {h["ysym"] for h in remaining}
        cands = [(r, syms[k]) for r, k in entry_candidates(state, live_px, thr, P) if syms[k] not in held_syms]
        if cands:
            summ = api.summary()
            cash = float(summ["cash"]["availableToTrade"])
            slot_eur = float(summ["totalValue"]) / P.slots
            for r, s in cands[:P.slots - len(remaining)]:
                amount = min(slot_eur, cash * 0.98)
                if amount < MIN_ORDER_EUR:
                    break
                px = float(live_px[col[s]])
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
    syms = [u[0] for u in UNIVERSE]
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
           f"10 € → {st['10€ se convierten en']} € (comprar y mantener: {st['comprar y mantener todo el universo']} €) · "
           f"{st['operaciones']} operaciones, {st['acierto %']} % ganadoras", "bar_chart")


def cmd_resolve():
    api = client_from_env()
    ins = api.instruments()
    tickers = resolve_tickers(ins)
    names = {i["ticker"]: i.get("name", "") for i in ins}
    for y, _, n, _ in UNIVERSE:
        t = tickers.get(y)
        print(f"{n:22s} {y:10s} ->  {t + '  (' + names.get(t, '') + ')' if t else 'NO ENCONTRADA'}")
    missing = [NAMES[u[0]] for u in UNIVERSE if u[0] not in tickers]
    notify("Universo comprobado", f"{len(tickers)}/{len(UNIVERSE)} acciones encontradas en Trading 212"
           + (f". Faltan: {', '.join(missing)}" if missing else ""), "white_check_mark")


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
