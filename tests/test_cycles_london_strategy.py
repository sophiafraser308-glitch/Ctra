"""Tests for strategies/cycles_london (pure Python; the sandbox test spawns the real runner process)."""
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
import app.strategies.sdk as sdk  # noqa: E402

sys.modules["strategy_sdk"] = sdk
SRC_PATH = os.path.join(ROOT, "strategies", "cycles_london", "strategy.py")
_spec = importlib.util.spec_from_file_location("cycles_london_strategy", SRC_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
PARAMS = dict(mod.STRATEGY_INFO["parameters"])
UTC = dt.timezone.utc
MIN = 60000


def ms(y, mo, d, h=0, mi=0):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def flat(t0, n, px, hi=0.0, lo=0.0):
    return [{"t": t0 + i * MIN, "o": px, "h": px + hi, "l": px - lo, "c": px, "v": 1} for i in range(n)]


def drive(params, bars, ctx=None, start=0):
    """Feed bars one by one exactly like the bot runner does (history = up to 200 previous bars)."""
    s = mod.CyclesLondon(dict(PARAMS, **params), ctx or {})
    s.on_start()
    out = []
    for i in range(start, len(bars)):
        r = s.on_bar("XAUUSD", "M1", bars[i], bars[max(0, i - 200):i])
        for x in r or []:
            out.append((bars[i]["t"], x))
    return out


S1_ONLY = {"s2_enabled": False, "cycle_clock": "utc"}
S2_ONLY = {"s1_enabled": False}


# ------------------------------------------------------------------ calendar helpers
def test_dst_helpers_match_zoneinfo():
    zi = __import__("zoneinfo")
    ldn, ny = zi.ZoneInfo("Europe/London"), zi.ZoneInfo("America/New_York")
    t = ms(2019, 1, 1)
    end = ms(2031, 1, 1)
    while t < end:
        d = dt.datetime.fromtimestamp(t / 1000, UTC)
        assert mod.london_offset_ms(t) == int(d.astimezone(ldn).utcoffset().total_seconds() * 1000), d
        assert mod.newyork_offset_ms(t) == int(d.astimezone(ny).utcoffset().total_seconds() * 1000), d
        t += 37 * 3600 * 1000


def test_dst_transition_instants():
    # UK 2026: starts Sun 29 Mar 01:00 UTC, ends Sun 25 Oct 01:00 UTC ; US 2026: Sun 8 Mar 07:00 UTC .. Sun 1 Nov 06:00 UTC
    assert mod.london_offset_ms(ms(2026, 3, 29, 0, 59)) == 0 and mod.london_offset_ms(ms(2026, 3, 29, 1, 0)) == 3600000
    assert mod.london_offset_ms(ms(2026, 10, 25, 0, 59)) == 3600000 and mod.london_offset_ms(ms(2026, 10, 25, 1, 0)) == 0
    assert mod.newyork_offset_ms(ms(2026, 3, 8, 6, 59)) == -5 * 3600000 and mod.newyork_offset_ms(ms(2026, 3, 8, 7, 0)) == -4 * 3600000
    assert mod.newyork_offset_ms(ms(2026, 11, 1, 5, 59)) == -4 * 3600000 and mod.newyork_offset_ms(ms(2026, 11, 1, 6, 0)) == -5 * 3600000


def test_digit_sum():
    assert mod.digit_sum(4150.37) == 10 and mod.digit_sum(3999.99) == 30 and mod.digit_sum(0.5) == 0


# ------------------------------------------------------------------ S1
def cycle_bars(t0, n=240, open_px=4150.37):
    """Cycle opening at open_px; price flat 4150.37 except the two entry bars."""
    bars = flat(t0, n, open_px)
    bars[10] = dict(bars[10], c=4151.0, h=4151.0)       # +10 min  -> close above level 4150.3 -> BUY
    bars[50] = dict(bars[50], c=4149.5, l=4149.5)       # +50 min  -> close below level -> SELL
    return bars


def test_s1_entries_levels_and_cycle_end_close():
    t0 = ms(2026, 3, 2, 0, 0)
    bars = flat(t0 - 30 * MIN, 30, 4100.0) + cycle_bars(t0)
    out = drive(dict(S1_ONLY), bars)
    kinds = [(t, x["side"], x.get("tag")) for t, x in out]
    # the first (partial) cycle is unknown to a freshly started bot: it only gets one harmless CLOSE at its end, no entries
    assert kinds == [(t0 - MIN, "CLOSE", "C"), (t0 + 10 * MIN, "BUY", "C"), (t0 + 50 * MIN, "CLOSE", "C"), (t0 + 50 * MIN, "SELL", "C"),
                     (t0 + 239 * MIN, "CLOSE", "C")], kinds
    assert out[2][1]["close_side"] == "BUY"                              # flip closes only the first (BUY) trade
    out = [out[1], out[3]]
    ind = [(t, x["side"]) for t, x in drive(dict(S1_ONLY, s1_mode="independent"), bars)]
    assert ind == [(t0 - MIN, "CLOSE"), (t0 + 10 * MIN, "BUY"), (t0 + 50 * MIN, "SELL"), (t0 + 239 * MIN, "CLOSE")], ind
    buy = out[0][1]
    assert buy["volume_lots"] == 0.10 and "L4150.3" in buy["comment"] and "+10m" in buy["comment"] and "stop_loss_pips" not in buy
    assert "E2" in out[1][1]["comment"] and "+50m" in out[1][1]["comment"]


def test_s1_close_equals_level_gives_no_trade_and_zero_minute_entry():
    t0 = ms(2026, 3, 2, 4, 0)
    bars = flat(t0 - 30 * MIN, 30, 4000.0) + flat(t0, 240, 4100.5)      # floor(4100.5)%100 = 0 -> E2 on the first bar; level 4100.5 == close
    out = drive(dict(S1_ONLY), bars)
    assert [x["side"] for _, x in out] == ["CLOSE"] and out[0][0] == t0 - MIN     # only the partial first cycle's CLOSE; close == level -> no entries
    bars2 = flat(t0 - 30 * MIN, 30, 4000.0) + flat(t0, 240, 4100.5)
    bars2[30] = dict(bars2[30], c=4100.9)                                # E2 entry bar (index 30 = t0) closes above level 4100.5 -> BUY
    out2 = drive(dict(S1_ONLY), bars2)
    assert [(t, x["side"]) for t, x in out2][1:2] == [(t0, "BUY")]
    assert out2[-1][1]["side"] == "CLOSE" and out2[-1][0] == t0 + 239 * MIN


def test_s1_tp_sl_lots_direction_and_flags():
    t0 = ms(2026, 3, 2, 0, 0)
    bars = flat(t0 - 30 * MIN, 30, 4100.0) + cycle_bars(t0)
    out = drive(dict(S1_ONLY, s1_tp_points=70.0, s1_sl_points=40.0, s1_lots=0.25), bars)
    e = [x for _, x in out if x["side"] != "CLOSE"]
    assert all(x["take_profit_pips"] == 70.0 and x["stop_loss_pips"] == 40.0 and x["volume_lots"] == 0.25 for x in e)
    sell_only = [x["side"] for _, x in drive(dict(S1_ONLY, trade_direction="sell"), bars)][1:]
    assert sell_only == ["SELL", "CLOSE"]                # BUY filtered by direction -> nothing to flip
    only_e1 = [x["side"] for _, x in drive(dict(S1_ONLY, s1_entry2_enabled=False), bars)][1:]
    assert only_e1 == ["BUY", "CLOSE"]
    risk = [x for _, x in drive(dict(S1_ONLY, lot_mode="risk"), bars) if x["side"] != "CLOSE"]
    assert risk and all("volume_lots" not in x for x in risk)


def test_s1_same_direction_second_signal_opens_both():
    t0 = ms(2026, 3, 2, 0, 0)
    bars = flat(t0 - 30 * MIN, 30, 4100.0) + cycle_bars(t0)
    bars[30 + 50] = dict(bars[30 + 50], c=4152.0, h=4152.0)               # E2 also above the level -> same direction
    out = [(t, x["side"]) for t, x in drive(dict(S1_ONLY), bars)][1:]
    assert out == [(t0 + 10 * MIN, "BUY"), (t0 + 50 * MIN, "BUY"), (t0 + 239 * MIN, "CLOSE")], out
    ind = [x["side"] for _, x in drive(dict(S1_ONLY, s1_mode="independent"), bars)][1:]
    assert ind == ["BUY", "BUY", "CLOSE"]


def test_s1_restart_midcycle_replays_silently():
    t0 = ms(2026, 3, 2, 0, 0)
    bars = flat(t0 - 30 * MIN, 30, 4100.0) + cycle_bars(t0)
    # bot starts at +30 min with 200 bars of history (cycle start is inside it): E1 (+10) is stale -> skipped, E2 (+50) still fires
    s = mod.CyclesLondon(dict(PARAMS, **S1_ONLY), {})
    s.on_start()
    i0 = 30 + 30
    r = s.on_bar("XAUUSD", "M1", bars[i0], bars[max(0, i0 - 200):i0])
    assert r is None
    out = []
    for i in range(i0 + 1, len(bars)):
        for x in s.on_bar("XAUUSD", "M1", bars[i], bars[max(0, i - 200):i]) or []:
            out.append((bars[i]["t"], x["side"]))
    assert out == [(t0 + 50 * MIN, "CLOSE"), (t0 + 50 * MIN, "SELL"), (t0 + 239 * MIN, "CLOSE")], out


def test_s1_unknown_cycle_start_skips_entries_but_still_closes():
    t0 = ms(2026, 3, 2, 0, 0)
    bars = cycle_bars(t0)
    s = mod.CyclesLondon(dict(PARAMS, **S1_ONLY), {})
    s.on_start()
    out = []
    for i in range(100, len(bars)):                       # bot started 100 min into the cycle with an EMPTY history
        for x in s.on_bar("XAUUSD", "M1", bars[i], bars[max(100, i - 200):i]) or []:
            out.append((bars[i]["t"], x["side"]))
    assert out == [(t0 + 239 * MIN, "CLOSE")], out


def test_s1_missing_bars_and_duplicates():
    t0 = ms(2026, 3, 2, 0, 0)
    bars = flat(t0 - 30 * MIN, 30, 4100.0) + cycle_bars(t0)
    s = mod.CyclesLondon(dict(PARAMS, **S1_ONLY), {})
    s.on_start()
    out = []
    for i in range(len(bars)):
        if 60 <= i < 100:
            continue                                      # 40 minutes of bars never delivered live (but present in history)
        for _ in range(2):                                # every bar delivered twice
            for x in s.on_bar("XAUUSD", "M1", bars[i], bars[max(0, i - 200):i]) or []:
                out.append((bars[i]["t"], x["side"]))
    assert out.count((t0 + 10 * MIN, "BUY")) == 1 and out.count((t0 + 239 * MIN, "CLOSE")) == 1


def test_cycle_clocks():
    t = ms(2026, 3, 2, 0, 0)
    s = mod.CyclesLondon(dict(PARAMS, cycle_clock="utc"), {})
    assert s._bucket(t) != s._bucket(t - MIN) and s._bucket(t + 239 * MIN) == s._bucket(t)
    s = mod.CyclesLondon(dict(PARAMS, cycle_clock="damascus"), {})     # UTC+3: boundaries 21:00, 01:00, 05:00 ... UTC
    assert s._bucket(ms(2026, 3, 2, 1, 0)) != s._bucket(ms(2026, 3, 2, 0, 59)) and s._bucket(ms(2026, 3, 2, 4, 0)) == s._bucket(ms(2026, 3, 2, 3, 0))
    s = mod.CyclesLondon(dict(PARAMS, cycle_clock="ny17"), {})         # 17:00 New York: 22:00 UTC in winter, 21:00 UTC in summer
    assert s._bucket(ms(2026, 1, 12, 22, 0)) != s._bucket(ms(2026, 1, 12, 21, 59))
    assert s._bucket(ms(2026, 7, 13, 21, 0)) != s._bucket(ms(2026, 7, 13, 20, 59))
    s = mod.CyclesLondon(dict(PARAMS, cycle_clock="custom", cycle_utc_offset_hours=2.0, cycle_anchor_hour=1), {})
    assert s._bucket(ms(2026, 3, 2, 23, 0)) != s._bucket(ms(2026, 3, 2, 22, 59))   # local 01:00


# ------------------------------------------------------------------ S2
def london_hour(day, hh, summer):
    """UTC ms of London-local hh:00 on `day` (y,m,d)."""
    return ms(day[0], day[1], day[2], hh - (1 if summer else 0), 0)


def s2_bars(t_win, breakout="up", n_before=40, n_after=90):
    """Window :30-:34 with H=4105 / L=4095, then flat inside the range, breakout bar 6 minutes after the window ended."""
    bars = flat(t_win - n_before * MIN, n_before, 4100.0, 0.2, 0.2)
    win = flat(t_win, 5, 4100.0, 0.2, 0.2)
    win[1] = dict(win[1], h=4105.0)
    win[3] = dict(win[3], l=4095.0)
    after = flat(t_win + 5 * MIN, n_after, 4100.0, 0.2, 0.2)
    after[6] = dict(after[6], c=4105.5 if breakout == "up" else 4094.5, h=4105.6, l=4094.4)
    return bars + win + after


def test_s2_breakout_buy_winter_and_close_before_next_half_hour():
    t_win = london_hour((2026, 2, 3), 10, False) + 30 * MIN          # 10:30 London == 10:30 UTC (GMT)
    out = drive(dict(S2_ONLY, s2_lots=0.3, s2_tp_points=80, s2_sl_points=50), s2_bars(t_win))
    assert [(t, x["side"], x.get("tag")) for t, x in out] == [(t_win + 11 * MIN, "BUY", "L"), (t_win + 59 * MIN, "CLOSE", "L")], out
    b = out[0][1]
    assert b["volume_lots"] == 0.3 and b["take_profit_pips"] == 80.0 and b["stop_loss_pips"] == 50.0 and "H4105.00" in b["comment"] and "L4095.00" in b["comment"]


def test_s2_breakout_sell_summer_uses_bst():
    t_win = london_hour((2026, 7, 7), 10, True) + 30 * MIN           # 10:30 London == 09:30 UTC (BST)
    assert dt.datetime.fromtimestamp(t_win / 1000, UTC).hour == 9
    out = drive(dict(S2_ONLY), s2_bars(t_win, "down"))
    assert [(x["side"], x["tag"]) for _, x in out] == [("SELL", "L"), ("CLOSE", "L")]
    assert out[0][0] == t_win + 11 * MIN


def test_s2_hour_range_and_first_breakout_only():
    t_win = london_hour((2026, 2, 3), 10, False) + 30 * MIN
    bars = s2_bars(t_win)
    assert drive(dict(S2_ONLY, s2_first_hour=11, s2_last_hour=23), bars) == []          # 10:30 window is outside the allowed hours
    bars[40 + 5 + 20] = dict(bars[40 + 5 + 20], c=4094.0, l=4093.0)                       # a later opposite breakout is ignored
    out = drive(dict(S2_ONLY), bars)
    assert [x["side"] for _, x in out] == ["BUY", "CLOSE"]


def test_s2_no_breakout_and_direction_filter():
    t_win = london_hour((2026, 2, 3), 10, False) + 30 * MIN
    bars = s2_bars(t_win)
    bars[40 + 5 + 6] = dict(bars[40 + 5 + 6], c=4100.0, h=4100.2, l=4099.8)               # undo the breakout
    assert drive(dict(S2_ONLY), bars) == []
    assert drive(dict(S2_ONLY, trade_direction="sell"), s2_bars(t_win)) == []              # BUY breakout filtered out


def test_s2_restart_after_window_rebuilds_levels_and_ignores_stale_breakout():
    t_win = london_hour((2026, 2, 3), 10, False) + 30 * MIN
    bars = s2_bars(t_win)
    i0 = 40 + 5 + 6 + 3                                                                   # bot started 3 min AFTER the breakout bar
    s = mod.CyclesLondon(dict(PARAMS, **S2_ONLY), {})
    s.on_start()
    out = []
    for i in range(i0, len(bars)):
        for x in s.on_bar("XAUUSD", "M1", bars[i], bars[max(0, i - 200):i]) or []:
            out.append(x["side"])
    assert out == ["CLOSE"]                                # stale breakout NOT traded, but the (possibly open) trade is closed on time
    s2 = mod.CyclesLondon(dict(PARAMS, **S2_ONLY), {})
    s2.on_start()
    i1 = 40 + 5 + 3                                         # started before the breakout: levels come from history, breakout traded live
    got = []
    for i in range(i1, len(bars)):
        for x in s2.on_bar("XAUUSD", "M1", bars[i], bars[max(0, i - 200):i]) or []:
            got.append((i, x["side"]))
    assert got[0] == (40 + 5 + 6, "BUY")


def test_s2_started_inside_window_ignores_incomplete_candle():
    t_win = london_hour((2026, 2, 3), 10, False) + 30 * MIN
    bars = s2_bars(t_win)
    i0 = 40 + 3                                             # first bar seen is :33 -> H/L of the 5m candle unknowable
    out = drive(dict(S2_ONLY), bars, start=i0)
    # history is passed too, so it IS known here (history contains :30..:32) -> trade expected; with no history it must not trade
    assert [x["side"] for _, x in out][:1] == ["BUY"]
    s = mod.CyclesLondon(dict(PARAMS, **S2_ONLY), {})
    s.on_start()
    got = []
    for i in range(i0, len(bars)):
        for x in s.on_bar("XAUUSD", "M1", bars[i], []) or []:
            got.append(x["side"])
    assert got == []


# ------------------------------------------------------------------ validation
def test_invalid_params_rejected():
    def bad(**kw):
        s = mod.CyclesLondon(dict(PARAMS, **kw), {})
        try:
            s.on_start()
        except ValueError as e:
            return str(e)
        return None
    assert "cycle_clock" in bad(cycle_clock="mars")
    assert "s2_first_hour" in bad(s2_first_hour=20, s2_last_hour=3)
    assert "trade_direction" in bad(trade_direction="x")
    assert "s1_mode" in bad(s1_mode="x")
    assert "s1_lots" in bad(s1_lots=0)
    assert "s2_tp_points" in bad(s2_tp_points=-1)
    s = mod.CyclesLondon(dict(PARAMS), {"timeframes": ["M15"]})
    try:
        s.on_start()
        assert False
    except ValueError as e:
        assert "M1" in str(e)
    s = mod.CyclesLondon(dict(PARAMS), {"timeframes": ["M1", "M5"]})
    s.on_start()


def test_other_timeframes_ignored():
    s = mod.CyclesLondon(dict(PARAMS), {})
    assert s.on_bar("XAUUSD", "M5", {"t": 0, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}, []) is None


# ------------------------------------------------------------------ real sandbox + backtest engine
def test_validator_accepts_file():
    try:
        from app.strategies.validator import validate_source
    except Exception:
        return                                              # pydantic missing in this environment
    src = open(SRC_PATH, encoding="utf-8").read()
    r = validate_source(src, allowed_imports={"math", "statistics", "collections", "dataclasses", "datetime", "decimal", "typing", "enum",
                                              "functools", "itertools", "fractions", "heapq", "bisect", "random", "json", "numbers", "operator"},
                        allowed_dependencies=set(), max_bytes=200000, max_dependencies=5)
    assert r.ok, r.errors
    assert r.class_name == "CyclesLondon" and r.metadata["name"] == "Cycles London"


def test_sandbox_plus_engine_tag_isolation():
    from app.backtest.engine import BacktestEngine, BtConfig, SymbolSpec
    runner = os.path.join(ROOT, "app", "strategies", "runner.py")
    sdk_src = open(os.path.join(ROOT, "app", "strategies", "sdk.py"), encoding="utf-8").read()
    src = open(SRC_PATH, encoding="utf-8").read()
    p = subprocess.Popen([sys.executable, "-I", runner], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="/tmp")

    def rq(i, method, params):
        p.stdin.write(json.dumps({"id": i, "method": method, "params": params}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())
    try:
        params = dict(PARAMS, cycle_clock="utc", s1_tp_points=0.0)
        init = {"source": src, "sdk_source": sdk_src, "class_name": "CyclesLondon", "allowed_imports": ["math", "datetime"], "params": params,
                "context": {"symbols": ["XAUUSD"], "timeframes": ["M1"]}, "memory_mb": 512, "cpu_seconds": 60}
        r = rq(1, "init", init)
        assert r["ok"], r
        assert rq(2, "on_start", {})["ok"]
        # 2026-03-02 (GMT): UTC cycle 08:00-11:59 ; London window 10:30 inside it -> both sub-strategies trade, each closed by its own tag
        t0 = ms(2026, 3, 2, 7, 30)
        bars = flat(t0, 30, 4100.0, 0.2, 0.2) + cycle_bars(ms(2026, 3, 2, 8, 0), 240, 4150.37)
        # make a London window at 10:30..10:34 (index = 30 + 150) and a breakout 6 minutes later
        base = 30 + 150
        for k in range(5):
            bars[base + k] = dict(bars[base + k], h=4150.9, l=4149.9)
        bars[base + 1] = dict(bars[base + 1], h=4151.5)
        bars[base + 11] = dict(bars[base + 11], c=4152.0, h=4152.0)              # close above window high 4151.5 -> S2 BUY
        items = [["XAUUSD", "M1", b, False] for b in bars]
        res = rq(3, "bt_chunk", {"base": 0, "items": items})
        assert res["ok"], res
        sig = {r_["i"]: r_["s"] for r_ in res["result"]}
        tags = [(i, s["side"], s.get("tag")) for i, ss in sorted(sig.items()) for s in ss]
        assert (30 + 10, "BUY", "C") in tags and (30 + 50, "SELL", "C") in tags and (base + 11, "BUY", "L") in tags
        assert (30 + 239, "CLOSE", "C") in tags and (base + 59, "CLOSE", "L") in tags       # London window 10:30 -> its trade is closed on the 11:29 bar
    finally:
        p.kill()


def test_engine_close_tag_filter():
    from app.backtest.engine import BacktestEngine, BtConfig, SymbolSpec
    TF = 60000
    e = BacktestEngine(BtConfig(tf_ms=TF, tz_offset_hours=0), {"XAUUSD": SymbolSpec(name="XAUUSD", pip_size=0.1, lot_units=100, digits=2, spread_pips=0.0)})

    def b(i, px):
        return i * TF, "XAUUSD", {"t": i * TF, "o": px, "h": px, "l": px, "c": px, "v": 1}
    buy_c = {"side": "BUY", "order_type": "MARKET", "volume_lots": 1.0, "tag": "C"}
    buy_l = {"side": "BUY", "order_type": "MARKET", "volume_lots": 1.0, "tag": "L"}
    e.feed(*b(0, 4100.0), [buy_c, buy_l])
    e.feed(*b(1, 4100.0), [{"side": "CLOSE", "tag": "C"}])      # both opened at bar 1 open; the CLOSE executes at bar 2 open
    assert sorted(p_.tag for p_ in e.open) == ["C", "L"]
    e.feed(*b(2, 4101.0), [])
    assert [p_.tag for p_ in e.open] == ["L"]
    e.feed(*b(3, 4101.0), [{"side": "CLOSE"}])                   # untagged CLOSE closes everything (backwards compatible)
    e.feed(*b(4, 4101.0), [])
    assert e.open == []
    assert [t_.reason for t_ in e.finish().trades] == ["SIGNAL_CLOSE", "SIGNAL_CLOSE"]
