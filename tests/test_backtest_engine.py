"""Backtest engine semantics (pure Python)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.backtest.engine import BacktestEngine, BtConfig, ConvSeries, SymbolSpec  # noqa: E402

TF = 900_000


def spec(**kw):
    d = dict(name="EURUSD", pip_size=0.0001, lot_units=100_000, digits=5, spread_pips=1.0)
    d.update(kw)
    return SymbolSpec(**d)


def bar(i, o, h, l, c):
    return i * TF, "EURUSD", {"t": i * TF, "o": o, "h": h, "l": l, "c": c, "v": 1}


def engine(**cfg):
    return BacktestEngine(BtConfig(tf_ms=TF, tz_offset_hours=0, **cfg), {"EURUSD": spec()})


BUY = {"side": "BUY", "order_type": "MARKET", "stop_loss_pips": 10, "take_profit_pips": 20, "volume_lots": 1.0}


def test_buy_fills_next_open_with_spread_and_hits_tp():
    e = engine()
    e.feed(*bar(0, 1.1000, 1.1005, 1.0995, 1.1000), [BUY])          # signal on bar 0 close
    e.feed(*bar(1, 1.1000, 1.1030, 1.0999, 1.1020), [])             # fills at open+1 pip = 1.1001 ; TP = 1.1021
    r = e.finish()
    t = r.trades[0]
    assert abs(t.entry_price - 1.1001) < 1e-9 and t.reason == "TP" and abs(t.exit_price - 1.1021) < 1e-9
    assert abs(t.pips - 20) < 1e-6 and abs(t.net - 200.0) < 1e-6          # 20 pips * $10/pip * 1 lot


def test_sl_wins_when_both_touched_in_same_bar():
    e = engine()
    e.feed(*bar(0, 1.1, 1.1, 1.1, 1.1), [BUY])
    e.feed(*bar(1, 1.1000, 1.1100, 1.0900, 1.1000), [])
    t = e.finish().trades[0]
    assert t.reason == "SL" and t.net < 0 and abs(t.pips + 10) < 1e-6


def test_sell_uses_ask_for_exit_and_commission():
    e = engine(commission_per_lot=7.0)
    sell = {"side": "SELL", "order_type": "MARKET", "stop_loss_pips": 10, "take_profit_pips": 10, "volume_lots": 0.5}
    e.feed(*bar(0, 1.1, 1.1, 1.1, 1.1), [sell])
    e.feed(*bar(1, 1.1000, 1.1002, 1.0985, 1.0990), [])    # entry 1.1000 ; TP 1.0990 hit when low+spread <= 1.0990 (1.0985+0.0001)
    t = e.finish().trades[0]
    assert t.reason == "TP" and abs(t.entry_price - 1.1) < 1e-9 and abs(t.pips - 10) < 1e-6
    assert abs(t.commission - 3.5) < 1e-9 and abs(t.net - (0.5 * 10 * 10 - 3.5)) < 1e-6


def test_signal_close_and_end_of_test_close():
    e = engine()
    e.feed(*bar(0, 1.1, 1.1, 1.1, 1.1), [BUY])
    e.feed(*bar(1, 1.1, 1.1002, 1.0998, 1.1001), [{"side": "CLOSE", "close_side": "BUY"}])
    e.feed(*bar(2, 1.1005, 1.1006, 1.1004, 1.1005), [BUY])
    e.feed(*bar(3, 1.1005, 1.1007, 1.1004, 1.1006), [])
    r = e.finish()
    assert [t.reason for t in r.trades] == ["SIGNAL_CLOSE", "END_OF_TEST"]


def test_risk_sizing_and_min_lots_skip():
    e = engine(initial_balance=10_000, risk_pct=1.0)
    sig = {"side": "BUY", "order_type": "MARKET", "stop_loss_pips": 20}          # no volume -> risk sizing
    e.feed(*bar(0, 1.1, 1.1, 1.1, 1.1), [sig])
    e.feed(*bar(1, 1.1, 1.1, 1.1, 1.1), [])
    assert abs(e.open[0].lots - 0.5) < 1e-9                                      # $100 / (20 pips * $10)
    e2 = engine(initial_balance=10_000)
    e2.feed(*bar(0, 1.1, 1.1, 1.1, 1.1), [{"side": "BUY", "order_type": "MARKET"}])
    e2.feed(*bar(1, 1.1, 1.1, 1.1, 1.1), [])
    assert e2.skipped and e2.skipped[0]["reason"] == "NO_STOP_LOSS_FOR_RISK_SIZING"


def test_quote_conversion_series():
    sp = spec(name="USDJPY", pip_size=0.01, lot_units=100_000, digits=3, quote_ccy="JPY", conv=ConvSeries([0], [1 / 150.0]))
    e = BacktestEngine(BtConfig(tf_ms=TF, tz_offset_hours=0), {"USDJPY": sp})
    sig = {"side": "BUY", "order_type": "MARKET", "stop_loss_pips": 10, "take_profit_pips": 10, "volume_lots": 1.0}
    e.feed(0, "USDJPY", {"t": 0, "o": 150.0, "h": 150.0, "l": 150.0, "c": 150.0, "v": 1}, [sig])
    e.feed(TF, "USDJPY", {"t": TF, "o": 150.0, "h": 150.5, "l": 149.99, "c": 150.3, "v": 1}, [])
    t = e.finish().trades[0]
    assert t.reason == "TP" and abs(t.net - (0.10 * 100_000 / 150.0)) < 1e-6     # 10 pips = 0.10 JPY * 100k / 150


def test_daily_loss_limit_stops_the_day():
    e = engine(daily_loss_limit=50.0)
    e.feed(*bar(0, 1.1, 1.1, 1.1, 1.1), [dict(BUY, stop_loss_pips=100, take_profit_pips=200)])
    e.feed(*bar(1, 1.1, 1.1001, 1.0950, 1.0990), [dict(BUY, stop_loss_pips=100, take_profit_pips=200)])   # worst floating ~ -$51
    e.feed(*bar(2, 1.0990, 1.1000, 1.0980, 1.0990), [])
    r = e.finish()
    assert r.stopped_days and r.trades[0].reason == "DAILY_LOSS_LIMIT"
    assert any("DAY_STOPPED" in s["reason"] for s in r.skipped)


def test_stats_consistency():
    e = engine()
    for k in range(6):
        e.feed(*bar(2 * k, 1.1, 1.1, 1.1, 1.1), [BUY])
        e.feed(*bar(2 * k + 1, 1.1000, 1.1030 if k % 2 == 0 else 1.1000, 1.0999 if k % 2 == 0 else 1.0980, 1.1), [])
    r = e.finish()
    s = r.stats
    assert s["total_trades"] == 6 and s["wins"] == 3 and s["losses"] == 3
    assert abs(s["net_profit"] - sum(t.net for t in r.trades)) < 1e-9
    assert abs(s["final_balance"] - r.trades[-1].balance_after) < 1e-9
    assert s["max_drawdown"] >= 0 and s["profit_factor"] is not None
