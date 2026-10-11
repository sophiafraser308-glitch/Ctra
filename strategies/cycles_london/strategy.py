"""4H Cycles + London 5m Breakout  (port of the TradingView indicator "4H Cycles + London 5m Breakout" v3).

RUN IT ON THE 1-MINUTE TIMEFRAME (M1). Two independent sub-strategies share the bot:

 S1  "4H cycles"
     * every 4-hour cycle starts on the first M1 bar of a new 4H candle;
     * level = open of that bar, floored to 0.1                      (4150.37 -> 4150.3)
     * entry 1 at  cycle start + digit-sum(integer part of the open) minutes     (4150 -> 4+1+5+0 = 10 min)
     * entry 2 at  cycle start + (integer part of the open mod 100) minutes      (4150 -> 50 min)
     * at each entry bar CLOSE > level -> BUY, CLOSE < level -> SELL (CLOSE == level -> no trade)
     * every S1 trade is closed on the last M1 bar of the cycle (unless TP/SL closed it earlier).

 S2  "London 5-minute breakout"
     * London time (DST aware) minutes :30-:34 of every allowed hour form a 5-minute candle (High/Low);
     * from :35 until the next :30 the first M1 CLOSE above High -> BUY, below Low -> SELL (one trade per hour);
     * the trade is closed on the last M1 bar before the next :30 (unless TP/SL closed it earlier).

Signals carry tag "C" (S1) or "L" (S2), so the end-of-cycle / end-of-hour CLOSE only closes its own sub-strategy.
TP / SL are expressed in PLATFORM POINTS (gold: 1 point = 0.1 price, so the Pine value 7.0 = 70 points). 0 = disabled.

Editable from Telegram:  Strategies -> Parameters (defaults)  /  Bots -> <bot> -> Settings (per bot).
"""
import math
from strategy_sdk import BaseStrategy

STRATEGY_INFO = {
    "name": "Cycles London",
    "version": "1.0.0",
    "description": "M1 port of the TradingView indicator: 4H cycle digit-sum entries (S1) + London 5-minute :30 breakout (S2).",
    "author": "platform",
    "parameters": {
        # ---- cycle clock (how the 4H candles are aligned) ----
        "cycle_clock": "ny17",
        "cycle_utc_offset_hours": 0.0,
        "cycle_anchor_hour": 0,
        # ---- S1: 4H cycles ----
        "s1_enabled": True,
        "s1_entry1_enabled": True,
        "s1_entry2_enabled": True,
        "s1_lots": 0.10,
        "s1_tp_points": 0.0,
        "s1_sl_points": 0.0,
        # ---- S2: London 5m breakout ----
        "s2_enabled": True,
        "s2_first_hour": 1,
        "s2_last_hour": 23,
        "s2_lots": 0.10,
        "s2_tp_points": 0.0,
        "s2_sl_points": 0.0,
        # ---- common ----
        "trade_direction": "both",
        "lot_mode": "fixed",
        "comment": "4HL",
    },
    "symbols": ["XAUUSD"],
    "timeframes": ["M1"],
    "dependencies": [],
}

MIN_MS = 60000
HOUR_MS = 3600000
DAY_MS = 86400000
H4_MS = 4 * HOUR_MS


# ---------------------------------------------------------------- calendar helpers (no tz database in the sandbox)
def days_from_civil(y, m, d):
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def year_of_days(z):
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    m = mp + (3 if mp < 10 else -9)
    return y + (1 if m <= 2 else 0)


def weekday_of_days(z):
    """0 = Sunday."""
    return (z + 4) % 7


def last_sunday(y, m):
    last = days_from_civil(y, m + 1, 1) - 1 if m < 12 else days_from_civil(y + 1, 1, 1) - 1
    return last - weekday_of_days(last)


def nth_sunday(y, m, n):
    first = days_from_civil(y, m, 1)
    return first + (7 - weekday_of_days(first)) % 7 + 7 * (n - 1)


def london_offset_ms(ms):
    """UK: BST (+1h) from the last Sunday of March 01:00 UTC to the last Sunday of October 01:00 UTC."""
    y = year_of_days(ms // DAY_MS)
    start = last_sunday(y, 3) * DAY_MS + HOUR_MS
    end = last_sunday(y, 10) * DAY_MS + HOUR_MS
    return HOUR_MS if start <= ms < end else 0


def newyork_offset_ms(ms):
    """US: EDT (-4h) from the 2nd Sunday of March 07:00 UTC to the 1st Sunday of November 06:00 UTC, else EST (-5h)."""
    y = year_of_days(ms // DAY_MS)
    start = nth_sunday(y, 3, 2) * DAY_MS + 7 * HOUR_MS
    end = nth_sunday(y, 11, 1) * DAY_MS + 6 * HOUR_MS
    return -4 * HOUR_MS if start <= ms < end else -5 * HOUR_MS


def digit_sum(price):
    n = int(math.floor(price))
    s = 0
    while n > 0:
        s += n % 10
        n = n // 10
    return s


class CyclesLondon(BaseStrategy):

    # ------------------------------------------------------------------ validation
    def on_start(self):
        p = self.params
        errs = []
        if p["cycle_clock"] not in ("ny17", "utc", "damascus", "custom"):
            errs.append("cycle_clock must be ny17, utc, damascus or custom")
        if not -12.0 <= float(p["cycle_utc_offset_hours"]) <= 14.0:
            errs.append("cycle_utc_offset_hours must be between -12 and 14")
        if int(p["cycle_anchor_hour"]) not in (0, 1, 2, 3):
            errs.append("cycle_anchor_hour must be 0, 1, 2 or 3")
        if not (0 <= int(p["s2_first_hour"]) <= 23 and 0 <= int(p["s2_last_hour"]) <= 23):
            errs.append("s2_first_hour / s2_last_hour must be 0..23")
        if int(p["s2_first_hour"]) > int(p["s2_last_hour"]):
            errs.append("s2_first_hour must be <= s2_last_hour")
        if p["trade_direction"] not in ("both", "buy", "sell"):
            errs.append("trade_direction must be both, buy or sell")
        if p["lot_mode"] not in ("fixed", "risk"):
            errs.append("lot_mode must be fixed or risk")
        if p["lot_mode"] == "fixed" and (float(p["s1_lots"]) <= 0 or float(p["s2_lots"]) <= 0):
            errs.append("s1_lots and s2_lots must be > 0")
        for key in ("s1_tp_points", "s1_sl_points", "s2_tp_points", "s2_sl_points"):
            if float(p[key]) < 0:
                errs.append(key + " must be >= 0 (0 = disabled)")
        tfs = self.context.get("timeframes") or []
        if tfs and "M1" not in tfs:
            errs.append("this strategy works on the M1 timeframe - add M1 to the bot's timeframes")
        if errs:
            raise ValueError("; ".join(errs))
        return None

    # ------------------------------------------------------------------ clocks
    def _bucket(self, ms):
        p = self.params
        clock = p["cycle_clock"]
        if clock == "ny17":
            local, anchor = ms + newyork_offset_ms(ms), 1          # 17:00 New York = 1 (mod 4)
        elif clock == "utc":
            local, anchor = ms, 0
        elif clock == "damascus":
            local, anchor = ms + 3 * HOUR_MS, 0
        else:
            local, anchor = ms + int(float(p["cycle_utc_offset_hours"]) * HOUR_MS), int(p["cycle_anchor_hour"])
        return (local - anchor * HOUR_MS) // H4_MS

    # ------------------------------------------------------------------ main hook
    def on_bar(self, symbol, timeframe, bar, history):
        if timeframe != "M1":
            return None
        st = self.state
        t = bar["t"]
        last = st.get("last_t")
        if last is not None and t <= last:
            return None                                   # same closed bar delivered twice
        missed = []
        i = len(history) - 1
        while i >= 0 and (last is None or history[i]["t"] > last):
            missed.append(history[i])
            i -= 1
        missed.reverse()
        for b in missed:                                  # catch-up / restart: rebuild the state silently, never trade on old bars
            self._step(symbol, b, None)
        out = []
        self._step(symbol, bar, out)
        return out or None

    # ------------------------------------------------------------------ one M1 bar (out=None -> silent replay)
    def _step(self, symbol, bar, out):
        st, p = self.state, self.params
        t, o, h, l, c = bar["t"], bar["o"], bar["h"], bar["l"], bar["c"]
        first_bar = st.get("last_t") is None
        st["last_t"] = t
        live = out is not None
        direction = p["trade_direction"]
        prefix = str(p["comment"])[:20]

        # deferred closes (a cycle / hour ended while we were not watching)
        if st.get("c_pc"):
            st["c_pc"] = False
            if live:
                out.append(self.close(symbol, prefix + " end", None, "C"))
        if st.get("l_pc"):
            st["l_pc"] = False
            if live:
                out.append(self.close(symbol, prefix + " end", None, "L"))

        # ===================== S1 : 4H cycles =====================
        b = self._bucket(t)
        prev_b = st.get("bucket")
        st["bucket"] = b
        if first_bar:
            st["c_known"] = False
        elif b != prev_b:
            if st.get("c_open"):
                st["c_open"] = False
                if live:
                    out.append(self.close(symbol, prefix + " end", None, "C"))
                else:
                    st["c_pc"] = True
            st["c_known"] = True
            st["lvl"] = math.floor(o * 10 + 0.000001) / 10
            st["t1"] = t + digit_sum(o) * MIN_MS
            st["t2"] = t + (int(math.floor(o)) % 100) * MIN_MS
            st["m1"] = digit_sum(o)
            st["m2"] = int(math.floor(o)) % 100
            st["d1"] = False
            st["d2"] = False
        if p["s1_enabled"] and st.get("c_known"):
            lvl = st["lvl"]
            for k in (1, 2):
                if st["d" + str(k)] or t < st["t" + str(k)]:
                    continue
                st["d" + str(k)] = True
                if k == 1 and not p["s1_entry1_enabled"]:
                    continue
                if k == 2 and not p["s1_entry2_enabled"]:
                    continue
                if c == lvl:
                    continue
                side = "BUY" if c > lvl else "SELL"
                if (direction == "buy" and side != "BUY") or (direction == "sell" and side != "SELL"):
                    continue
                st["c_open"] = True
                if live:
                    out.append(self._open(symbol, side, "s1", "%s E%d L%.1f +%dm" % (prefix, k, lvl, st["m" + str(k)]), "C"))
            # last M1 bar of the cycle -> close (executed at the next bar's open, i.e. ~ the cycle's final close)
            if st.get("c_open") and self._bucket(t + MIN_MS) != b:
                st["c_open"] = False
                if live:
                    out.append(self.close(symbol, prefix + " end", None, "C"))
                else:
                    st["c_pc"] = True
        elif p["s1_enabled"] and not st.get("c_known") and self._bucket(t + MIN_MS) != b:
            if live:                                      # started mid-cycle without enough history: close anything left over
                out.append(self.close(symbol, prefix + " end", None, "C"))
            st["c_known"] = True                          # the next bar starts a fresh cycle anyway

        # ===================== S2 : London 5m breakout =====================
        if not p["s2_enabled"]:
            return
        lt = t + london_offset_ms(t)
        hr = (lt // HOUR_MS) % 24
        mn = (lt // MIN_MS) % 60
        in_win = int(p["s2_first_hour"]) <= hr <= int(p["s2_last_hour"]) and 30 <= mn < 35

        if st.get("actB"):
            if t + MIN_MS >= st["expB"] and st.get("l_open"):       # last bar before the next :30
                st["l_open"] = False
                if live:
                    out.append(self.close(symbol, prefix + " end", None, "L"))
                else:
                    st["l_pc"] = True
            if t >= st["expB"]:
                st["actB"] = False
        if st.get("acc") and (not in_win or t - st["accT"] >= 10 * MIN_MS):
            st["acc"] = False
            st["hiB"], st["loB"], st["expB"] = st["accH"], st["accL"], st["accT"] + 60 * MIN_MS
            st["actB"], st["brkB"] = True, False
        if not in_win:
            st["blk"] = False
        if in_win and not st.get("blk"):
            if not st.get("acc"):
                if first_bar and mn != 30:
                    st["blk"] = True                       # joined in the middle of the 5m candle: its H/L is incomplete
                else:
                    st["acc"], st["accT"], st["accH"], st["accL"] = True, t, h, l
            else:
                st["accH"] = max(st["accH"], h)
                st["accL"] = min(st["accL"], l)
        if st.get("actB") and not st.get("brkB"):
            up = c > st["hiB"]
            dn = c < st["loB"]
            if up or dn:
                st["brkB"] = True
                side = "BUY" if up else "SELL"
                if (direction == "buy" and side != "BUY") or (direction == "sell" and side != "SELL"):
                    return
                st["l_open"] = True
                if live:
                    out.append(self._open(symbol, side, "s2", "%s S2 H%.2f L%.2f" % (prefix, st["hiB"], st["loB"]), "L"))

    # ------------------------------------------------------------------ order builder
    def _open(self, symbol, side, which, comment, tag):
        p = self.params
        sl = float(p[which + "_sl_points"])
        tp = float(p[which + "_tp_points"])
        lots = float(p[which + "_lots"]) if p["lot_mode"] == "fixed" else None
        sl = sl if sl > 0 else None
        tp = tp if tp > 0 else None
        if side == "BUY":
            return self.buy(symbol, sl, tp, comment, "MARKET", None, lots, tag)
        return self.sell(symbol, sl, tp, comment, "MARKET", None, lots, tag)
