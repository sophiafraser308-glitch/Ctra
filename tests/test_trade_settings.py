"""Trade settings (lot / SL / TP per timeframe): parsing, resolution, backtest integration. Pure Python."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.backtest.engine import BacktestEngine, BtConfig, SymbolSpec  # noqa: E402
from app.core import trade_settings as T  # noqa: E402
from app.core.exceptions import ValidationFailed  # noqa: E402


@pytest.mark.parametrize("raw,tf", [("1m", "M1"), ("5m", "M5"), ("15m", "M15"), ("30M", "M30"), ("1h", "H1"), ("4H", "H4"), ("1d", "D1"),
                                    ("1w", "W1"), ("1mn", "MN1"), ("M15", "M15"), ("h4", "H4"), ("MN1", "MN1"), ("15min", "M15"), ("d", "D1"),
                                    ("١٥m", "M15")])
def test_parse_tf(raw, tf):
    assert T.parse_tf(raw) == tf


@pytest.mark.parametrize("raw", ["", "x", "7m", "15", "1m1", "h", "99d"])
def test_parse_tf_rejects(raw):
    with pytest.raises(ValidationFailed):
        T.parse_tf(raw)


def test_parse_points_and_lot():
    assert T.parse_points("150") == 150.0 and T.parse_points("٥٠") == 50.0 and T.parse_points("12,5") == 12.5
    assert T.parse_points("0") == 0.0 and T.parse_points("off") == 0.0
    for bad in ("-1", "abc", "1e9", "nan"):
        with pytest.raises(ValidationFailed):
            T.parse_points(bad)
    assert T.parse_lot("0.1") == 0.1 and T.parse_lot("0") == 0.0
    with pytest.raises(ValidationFailed):
        T.parse_lot("-0.1")


def test_defaults_keep_the_old_strategy_values():
    ts = T.normalize(None)
    assert (ts["lot_size"], ts["sl_default"], ts["tp_default"], ts["multi_tf"]) == (0.10, 30.0, 60.0, True)


def test_per_timeframe_values_and_default_fallback():
    ts = T.normalize(None)
    ts = T.with_value(ts, "tp", "M1", 50)
    ts = T.with_value(ts, "sl", "M1", 90)
    ts = T.with_value(ts, "tp", "M15", 150)
    m1, m15, h1 = T.effective(ts, "M1"), T.effective(ts, "M15"), T.effective(ts, "H1")
    assert (m1["tp"], m1["sl"]) == (50.0, 90.0)
    assert (m15["tp"], m15["sl"]) == (150.0, 30.0) and m15["sl_src"] == "default"     # SL falls back to the default
    assert (h1["tp"], h1["sl"]) == (60.0, 30.0)
    assert T.effective(ts, None)["tp"] == 60.0


def test_zero_disables_and_default_keyword_removes_override():
    ts = T.with_value(T.normalize(None), "tp", "M5", 0)
    assert T.effective(ts, "M5")["tp"] is None                       # explicit "no TP on M5"
    ts = T.with_value(ts, "tp", "M5", None)
    assert T.effective(ts, "M5")["tp"] == 60.0                       # back to the default
    with pytest.raises(ValidationFailed):
        T.with_value(ts, "tp", None, None)


def test_normalize_survives_garbage():
    ts = T.normalize({"lot_size": "x", "sl": {"M1": "bad", "ZZ": 5, "M5": 20}, "tp": 7, "multi_tf": "yes", "sl_default": -4})
    assert ts["lot_size"] == 0.10 and ts["sl"] == {"M5": 20.0} and ts["tp"] == {} and ts["multi_tf"] is True and ts["sl_default"] == 30.0


def test_lot_zero_means_risk_sizing():
    assert T.effective(T.normalize({"lot_size": 0}), "M1")["lot"] is None
    assert T.effective(T.normalize({"lot_size": 0.25}), "M1")["lot"] == 0.25


def test_backtest_levels_priority():
    ts = T.with_value(T.normalize(None), "sl", "M15", 90)
    assert T.backtest_levels(ts, "M15", sl_opt=200)["sl"] == 90.0           # the timeframe's own value beats the wizard option
    assert T.backtest_levels(ts, "M5", sl_opt=200)["sl"] == 200.0           # wizard option beats the default
    assert T.backtest_levels(ts, "M5")["sl"] == 30.0                        # otherwise the default
    assert T.backtest_levels(ts, "M5", lot_opt=0.5)["lot"] == 0.5


def test_panel_text_marks_inherited_and_warns_without_sl():
    ts = T.with_value(T.normalize(None), "tp", "M15", 150)
    txt = T.panel_text(ts, tfs_in_use=["M1"])
    assert "M15" in txt and "150" in txt and "M1 " in txt and "*" in txt
    no_sl = T.panel_text(T.with_value(T.normalize(None), "sl", None, 0))
    assert "No stop loss" in no_sl


# ---- backtest engine honours the forced per-timeframe levels ---------------------------------------------------------
TF = 900_000


def _engine(**cfg):
    spec = SymbolSpec(name="XAUUSD", pip_size=0.1, lot_units=100, digits=2, spread_pips=0.0)
    return BacktestEngine(BtConfig(tf_ms=TF, tz_offset_hours=0, **cfg), {"XAUUSD": spec})


def _bar(i, o, h, l, c):
    return i * TF, "XAUUSD", {"t": i * TF, "o": o, "h": h, "l": l, "c": c, "v": 1}


def test_engine_forced_levels_replace_strategy_values_and_zero_means_none():
    sig = {"side": "BUY", "order_type": "MARKET", "stop_loss_pips": 10, "take_profit_pips": 20, "volume_lots": 1.0}
    e = _engine(sl_override=90.0, tp_override=0.0, sl_forced=True, tp_forced=True, lot_override=0.1)
    e.feed(*_bar(0, 4150, 4150, 4150, 4150), [sig])
    e.feed(*_bar(1, 4150, 4151, 4149, 4150), [])
    pos = e.open[0]
    assert pos.sl_pips == 90.0 and pos.tp_pips is None and pos.lots == 0.1       # strategy's 10/20/1.0 ignored; TP forced off
    assert abs(pos.sl - (4150 - 90 * 0.1)) < 1e-9


def test_engine_unforced_keeps_legacy_behaviour():
    sig = {"side": "BUY", "order_type": "MARKET", "stop_loss_pips": 10, "take_profit_pips": 20, "volume_lots": 1.0}
    e = _engine()
    e.feed(*_bar(0, 4150, 4150, 4150, 4150), [sig])
    e.feed(*_bar(1, 4150, 4150.5, 4149.5, 4150), [])          # neither the 1.0 stop nor the 2.0 target is touched
    assert e.open[0].sl_pips == 10 and e.open[0].tp_pips == 20
