import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot  # noqa: E402
import strategy as S  # noqa: E402

TODAY = pd.Timestamp(dt.datetime.now(bot.TZ).date())
SYMS = [y for y, _, _ in bot.UNIVERSE]


def test_rsi_bounds():
    assert S.rsi(pd.Series(np.arange(1, 50, dtype=float)), 2).iloc[-1] == 100.0
    assert S.rsi(pd.Series(np.arange(50, 1, -1, dtype=float)), 2).iloc[-1] < 1.0
    r = S.rsi(pd.Series(100 + np.random.default_rng(0).standard_normal(500).cumsum()), 2).dropna()
    assert r.between(0, 100).all()


def test_entry_and_exit_rules():
    p = S.Params()
    up = 100 * 1.001 ** np.arange(260)
    assert S.entry_signal(up, up[-1] * 1.01, p) is None           # sube: no compra
    assert S.entry_signal(up, up[-1] * 0.97, p) is not None       # caída en tendencia alcista: compra
    down = 100 * 0.999 ** np.arange(260)
    assert S.entry_signal(down, down[-1] * 0.97, p) is None       # bajo la SMA200: no compra
    prior = np.array([100, 100, 100, 100, 100.0])
    assert S.exit_reason(prior, 101, 99, 0, False, p) is None     # nunca el mismo día
    assert S.exit_reason(prior, 101, 99, 1, False, p).startswith("rebote")
    assert S.exit_reason(prior, 89, 100, 0, False, p).startswith("stop")
    assert S.exit_reason(prior, 99, 99, 10, True, p).startswith("tiempo")
    assert S.exit_reason(prior, 99, 99, 10, False, p) is None


def test_round_down():
    assert bot.round_down(0.123456, 4) == 0.1234
    assert bot.round_down(0.99, 0) == 0.0


def make_daily(dips=(), n=260, with_today=True):
    end = TODAY if with_today else TODAY - pd.Timedelta(days=1)
    idx = pd.bdate_range(end=end, periods=n)
    data = {}
    for i, y in enumerate(SYMS):
        s = 100 * (1.001 ** np.arange(n)) * (1 + 0.002 * np.sin(np.arange(n) + i))
        if y in dips:
            s[-3:] = s[-4] * np.array([0.98, 0.96, 0.94])
        data[y] = s
    return pd.DataFrame(data, index=idx)


class FakeT212:
    env = "live"

    def __init__(self, positions=None, history=None, cash=10.0, total=10.0):
        self._positions = positions or []
        self._history = history or {}
        self.cash, self.total = cash, total
        self.orders = []

    def instruments(self):
        return [{"isin": isin, "ticker": y.replace(".", "_") + "_EQ", "currencyCode": "EUR", "type": "STOCK",
                 "addedOn": "2020-01-01"} for y, isin, _ in bot.UNIVERSE] + \
               [{"isin": bot.UNIVERSE[0][1], "ticker": "SAP_US_EQ", "currencyCode": "USD", "type": "STOCK"}]

    def positions(self):
        return self._positions

    def history(self, ticker, limit=10):
        items = list(self._history.get(ticker, []))
        for o in self.orders:
            if o["ticker"] == ticker:
                items.insert(0, {"order": {"id": o["id"], "side": "BUY" if o["quantity"] > 0 else "SELL",
                                           "status": "FILLED", "initiatedFrom": "API"},
                                 "fill": {"price": 94.0, "filledAt": "2026-01-01T15:00:00Z",
                                          "walletImpact": {"realisedProfitLoss": 0.03}}})
        return {"items": items}

    def summary(self):
        return {"cash": {"availableToTrade": self.cash}, "totalValue": self.total,
                "investments": {"realizedProfitLoss": 0.0}}

    def market_order(self, ticker, quantity):
        if abs(quantity) != round(abs(quantity), 2):
            raise bot.T212Error(400, "quantity precision mismatch")
        o = {"id": len(self.orders) + 1, "ticker": ticker, "quantity": quantity}
        self.orders.append(o)
        return o


@pytest.fixture
def env(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "notify", lambda t, m, *a, **k: sent.append((t, m)))
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.delenv("DRY_RUN", raising=False)
    return sent


def _pos(ticker, qty=0.02, avg=90.0, cur=100.0):
    return {"instrument": {"ticker": ticker}, "quantity": qty, "quantityAvailableForTrading": qty,
            "averagePricePaid": avg, "currentPrice": cur,
            "walletImpact": {"unrealizedProfitLoss": 0.2, "totalCost": 1.8}}


def _hist(initiated="API", days_ago=3):
    t = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_ago)).isoformat()
    return [{"order": {"id": 99, "side": "BUY", "status": "FILLED", "initiatedFrom": initiated},
             "fill": {"price": 90.0, "filledAt": t}}]


def test_resolve_ignores_usd_listing():
    t = bot.resolve_tickers(FakeT212().instruments())
    assert t["SAP.DE"] == "SAP_DE_EQ" and len(t) == len(bot.UNIVERSE)


def test_fills_slots_with_most_oversold(env, monkeypatch):
    fake = FakeT212(cash=10.0, total=10.0)
    monkeypatch.setattr(bot, "client_from_env", lambda: fake)
    monkeypatch.setattr(bot, "fetch_daily", lambda syms: make_daily(("IBE.MC", "SAP.DE"))[syms])
    bot.cmd_run(force=True)
    buys = [o for o in fake.orders if o["quantity"] > 0]
    assert {o["ticker"] for o in buys} == {"IBE_MC_EQ", "SAP_DE_EQ"}
    for o in buys:  # cada hueco = 1/5 del capital, precio ≈ 94-120
        assert 0 < o["quantity"] * 94 <= 2.0 + 1e-6
    assert sum(t.startswith("COMPRADA") for t, _ in env) == 2


def test_no_buys_outside_entry_window(env, monkeypatch):
    fake = FakeT212()
    monkeypatch.setattr(bot, "client_from_env", lambda: fake)
    monkeypatch.setattr(bot, "fetch_daily", lambda syms: make_daily(("IBE.MC",))[syms])
    monkeypatch.setattr(bot, "in_session", lambda now: True)
    monkeypatch.setattr(bot, "ENTRY_START", dt.time(23, 59))
    bot.cmd_run(force=False)
    assert fake.orders == []


def test_sells_rebound_with_realtime_price(env, monkeypatch):
    daily = make_daily(())
    last = float(daily["SAP.DE"].iloc[-2])
    fake = FakeT212(positions=[_pos("SAP_DE_EQ", 0.023456, avg=last * 0.97, cur=last * 1.02)],
                    history={"SAP_DE_EQ": _hist()}, cash=0.0)
    monkeypatch.setattr(bot, "client_from_env", lambda: fake)
    monkeypatch.setattr(bot, "fetch_daily", lambda syms: daily[syms])
    bot.cmd_run(force=True)
    sells = [o for o in fake.orders if o["quantity"] < 0]
    assert len(sells) == 1 and sells[0]["quantity"] == -0.02
    assert any(t.startswith("VENDIDA SAP") for t, _ in env)


def test_never_touches_manual_positions(env, monkeypatch):
    daily = make_daily(())
    last = float(daily["SAP.DE"].iloc[-2])
    fake = FakeT212(positions=[_pos("SAP_DE_EQ", avg=last * 0.97, cur=last * 1.02)],
                    history={"SAP_DE_EQ": _hist(initiated="IOS")}, cash=0.0)
    monkeypatch.setattr(bot, "client_from_env", lambda: fake)
    monkeypatch.setattr(bot, "fetch_daily", lambda syms: daily[syms])
    bot.cmd_run(force=True)
    assert not [o for o in fake.orders if o["quantity"] < 0]


def test_holiday_skips(env, monkeypatch):
    fake = FakeT212()
    monkeypatch.setattr(bot, "client_from_env", lambda: fake)
    monkeypatch.setattr(bot, "fetch_daily", lambda syms: make_daily(("IBE.MC",), with_today=False)[syms])
    monkeypatch.setattr(bot, "in_session", lambda now: True)
    bot.cmd_run(force=False)
    assert fake.orders == [] and env == []


def test_session_window():
    tz = bot.TZ
    assert bot.in_session(dt.datetime(2026, 9, 23, 9, 30, tzinfo=tz))
    assert not bot.in_session(dt.datetime(2026, 9, 23, 17, 40, tzinfo=tz))
    assert not bot.in_session(dt.datetime(2026, 9, 26, 12, 0, tzinfo=tz))  # sábado


def test_hourly_backtest_runs():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2024-01-01", periods=400, freq="B")
    hours = [pd.Timestamp(d) + pd.Timedelta(hours=h) for d in idx for h in range(9, 18)]
    hidx = pd.DatetimeIndex(hours).tz_localize(bot.TZ)
    df = pd.DataFrame({f"S{i}": 100 * np.exp(np.cumsum(0.0001 + 0.004 * rng.standard_normal(len(hidx))))
                       for i in range(6)}, index=hidx)
    eq, tr, bench = S.backtest_hourly(df, S.Params())
    assert len(eq) > 100 and (eq > 0).all()
    assert len(tr) > 0 and ((tr["dias"] >= 1) | tr["motivo"].str.startswith("stop")).all()
