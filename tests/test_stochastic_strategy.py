"""Pure-Python test of the Stochastic Cross strategy (no third-party packages needed)."""
import importlib.util
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
import app.strategies.sdk as sdk  # noqa: E402

sys.modules["strategy_sdk"] = sdk
_spec = importlib.util.spec_from_file_location("stoch_strategy", os.path.join(ROOT, "strategies", "stochastic_cross", "strategy.py"))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
PARAMS = dict(mod.STRATEGY_INFO["parameters"])


def wave(n, period=140, amp=0.02):
    out = []
    for i in range(n):
        c = 1.10 + amp * math.sin(2 * math.pi * i / period) + 0.002 * math.sin(i / 3.0)
        out.append({"t": i * 900_000, "o": c, "h": c + 0.0004, "l": c - 0.0004, "c": c, "v": 1})
    return out


def run(params, n=900):
    s = mod.StochasticCross(params, {})
    s.on_start()
    bars, signals = wave(n), []
    for i in range(1, len(bars)):
        hist = bars[max(0, i - 190):i]
        r = s.on_bar("EURUSD", "M15", bars[i], hist)
        if r:
            signals.extend((i, x) for x in r)
    return signals


def test_stochastic_in_range_and_warmup():
    bars = wave(300)
    c = [b["c"] for b in bars]
    main, sig = mod.stochastic(c, c, c, 49, 15, 5, "lwma")
    vals = [v for v in main if v is not None]
    assert vals and all(-1e-9 <= v <= 100 + 1e-9 for v in vals)
    assert main[49 + 15 - 3] is None and main[49 + 15 - 2] is not None
    assert sig[49 + 15 - 2 + 3] is None and sig[49 + 15 - 2 + 4] is not None


def test_lwma_weights():
    out = mod._ma_series([1.0, 2.0, 3.0], 3, "lwma")
    assert abs(out[2] - (1 * 1 + 2 * 2 + 3 * 3) / 6) < 1e-12


def test_signals_carry_no_size_or_protection_and_level_logic():
    # lot / SL / TP are applied by the platform from the Telegram trade settings, never by the strategy
    assert not {"lot_mode", "fixed_lots", "sl_pips", "tp_pips"} & set(PARAMS)
    sigs = run(PARAMS)
    entries = [(i, x) for i, x in sigs if x["side"] in ("BUY", "SELL")]
    assert any(x["side"] == "BUY" for _, x in entries) and any(x["side"] == "SELL" for _, x in entries)
    for _, x in entries:
        assert "volume_lots" not in x and "stop_loss_pips" not in x and "take_profit_pips" not in x
    bars = wave(900)
    c = [b["c"] for b in bars]
    main, _ = mod.stochastic(c, c, c, 49, 15, 5, "lwma")
    for i, x in entries:   # entry fired on bar i must be a real crossing of the configured level
        prev, cur = main[i - 1], main[i]
        if x["side"] == "BUY":
            assert prev <= 10 < cur
        else:
            assert prev >= 90 > cur


def test_direction_filter():
    p = dict(PARAMS, trade_direction="buy")
    sigs = run(p)
    entries = [x for _, x in sigs if x["side"] in ("BUY", "SELL")]
    assert entries and all(x["side"] == "BUY" and "volume_lots" not in x for x in entries)


def test_invalid_params_rejected():
    s = mod.StochasticCross(dict(PARAMS, ma_method="bogus", k_period=0), {})
    try:
        s.on_start()
        assert False
    except ValueError as e:
        assert "ma_method" in str(e)
