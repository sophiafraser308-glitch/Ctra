"""Stochastic Cross - MT5-style Stochastic Oscillator (default 49 / 5 / 15, Close/Close, Linear Weight).

BUY  : the stochastic line crosses UP through the buy level  (default 10)
SELL : the stochastic line crosses DOWN through the sell level (default 90)

Indicator numbers below are editable from Telegram:
  * Strategies -> Parameters       (defaults for every new bot)
  * Bots -> <bot> -> Settings      (per-bot override; the bot must be stopped)

Lot size, stop loss and take profit are NOT in this file any more: they are controlled from Telegram
(Settings -> Trade settings, or  /set tp 15m 150 ,  /set sl 15m 90 ,  /set lot 0.1 ) per timeframe, for live, demo and backtest.
"""
from strategy_sdk import BaseStrategy

STRATEGY_INFO = {
    "name": "Stochastic Cross",
    "version": "1.1.0",
    "description": "Stochastic (MT5 style) - buy on cross up through level 10, sell on cross down through level 90.",
    "author": "platform",
    "parameters": {
        # ---- indicator (same fields as the MT5 dialog) ----
        "k_period": 49,
        "d_period": 5,
        "slowing": 15,
        "price_field": "close",
        "ma_method": "lwma",
        "signal_line": "main",
        # ---- levels ----
        "buy_level": 10.0,
        "sell_level": 90.0,
        "upper_level": 80.0,
        "lower_level": 20.0,
        # ---- trade rules ----
        "trade_direction": "both",
        "reverse_signals": False,
        "close_opposite": True,
        "use_level_exit": False,
        "min_bars_between_trades": 0,
        # lot size / SL / TP live in the platform's Trade settings (Telegram), not here
        "comment": "STO",
    },
    "symbols": ["EURUSD"],
    "timeframes": ["M15"],
    "dependencies": [],
}

MAX_HISTORY = 190           # the platform provides ~200 closed bars of history per call


def _ma_series(vals, n, method):
    """Moving average of a list without gaps. Index i uses vals[i-n+1..i]; earlier entries are None."""
    out = [None] * len(vals)
    if n <= 1:
        return list(vals)
    if len(vals) < n:
        return out
    if method == "sma":
        for i in range(n - 1, len(vals)):
            out[i] = sum(vals[i - n + 1:i + 1]) / n
    elif method == "lwma":
        wsum = n * (n + 1) / 2.0
        for i in range(n - 1, len(vals)):
            seg = vals[i - n + 1:i + 1]
            s = 0.0
            for j in range(n):
                s += seg[j] * (j + 1)
            out[i] = s / wsum
    elif method == "ema":
        k = 2.0 / (n + 1)
        prev = sum(vals[:n]) / n
        out[n - 1] = prev
        for i in range(n, len(vals)):
            prev = vals[i] * k + prev * (1 - k)
            out[i] = prev
    else:  # smma
        prev = sum(vals[:n]) / n
        out[n - 1] = prev
        for i in range(n, len(vals)):
            prev = (prev * (n - 1) + vals[i]) / n
            out[i] = prev
    return out


def stochastic(highs, lows, closes, k, slowing, d, method):
    """MT5 formula: %K(main) = 100 * sum(close-LL, slowing) / sum(HH-LL, slowing); %D(signal) = MA(main, d, method)."""
    n = len(closes)
    nums = [None] * n
    dens = [None] * n
    for i in range(k - 1, n):
        ll = min(lows[i - k + 1:i + 1])
        hh = max(highs[i - k + 1:i + 1])
        nums[i] = closes[i] - ll
        dens[i] = hh - ll
    main = [None] * n
    start = k - 1 + slowing - 1
    for i in range(start, n):
        sn = sum(nums[i - slowing + 1:i + 1])
        sd = sum(dens[i - slowing + 1:i + 1])
        if sd > 0:
            main[i] = 100.0 * sn / sd
        else:
            main[i] = main[i - 1] if (i > start and main[i - 1] is not None) else 50.0
    signal = [None] * n
    if n > start:
        sm = _ma_series(main[start:], d, method)
        for j in range(len(sm)):
            signal[start + j] = sm[j]
    return main, signal


def crossed_up(prev, cur, level):
    return prev <= level and cur > level


def crossed_down(prev, cur, level):
    return prev >= level and cur < level


class StochasticCross(BaseStrategy):

    def on_start(self):
        p = self.params
        errs = []
        for key in ("k_period", "d_period", "slowing"):
            if int(p[key]) < 1:
                errs.append(key + " must be >= 1")
        if int(p["k_period"]) + int(p["slowing"]) + int(p["d_period"]) + 3 > MAX_HISTORY:
            errs.append("k_period + slowing + d_period must be <= " + str(MAX_HISTORY - 3) + " (available history)")
        if p["price_field"] not in ("close", "lowhigh"):
            errs.append("price_field must be close or lowhigh")
        if p["ma_method"] not in ("sma", "ema", "smma", "lwma"):
            errs.append("ma_method must be sma, ema, smma or lwma")
        if p["signal_line"] not in ("main", "signal"):
            errs.append("signal_line must be main or signal")
        if p["trade_direction"] not in ("both", "buy", "sell"):
            errs.append("trade_direction must be both, buy or sell")
        for key in ("buy_level", "sell_level", "upper_level", "lower_level"):
            if not 0 <= float(p[key]) <= 100:
                errs.append(key + " must be between 0 and 100")
        if int(p["min_bars_between_trades"]) < 0:
            errs.append("min_bars_between_trades must be >= 0")
        if errs:
            raise ValueError("; ".join(errs))
        return None

    def on_bar(self, symbol, timeframe, bar, history):
        p = self.params
        k, slowing, d = int(p["k_period"]), int(p["slowing"]), int(p["d_period"])
        bars = list(history) + [bar]
        if len(bars) < k + slowing + d + 3:
            return None
        key = symbol + ":" + timeframe
        if self.state.get(key + ":t") == bar["t"]:
            return None                                  # same closed bar delivered twice
        self.state[key + ":t"] = bar["t"]

        closes = [b["c"] for b in bars]
        if p["price_field"] == "lowhigh":
            highs, lows = [b["h"] for b in bars], [b["l"] for b in bars]
        else:
            highs, lows = closes, closes
        main, sig = stochastic(highs, lows, closes, k, slowing, d, p["ma_method"])
        line = main if p["signal_line"] == "main" else sig
        cur, prev = line[-1], line[-2]
        if cur is None or prev is None:
            return None

        cool = int(self.state.get(key + ":cool", 0))
        blocked = cool > 0
        if cool > 0:
            self.state[key + ":cool"] = cool - 1

        up_buy = crossed_up(prev, cur, float(p["buy_level"]))
        down_sell = crossed_down(prev, cur, float(p["sell_level"]))
        reverse = bool(p["reverse_signals"])
        direction = p["trade_direction"]
        out = []

        if bool(p["use_level_exit"]):
            if crossed_down(prev, cur, float(p["upper_level"])):
                out.append(self.close(symbol, "exit upper", "BUY"))
            if crossed_up(prev, cur, float(p["lower_level"])):
                out.append(self.close(symbol, "exit lower", "SELL"))

        side = None
        if up_buy:
            side = "SELL" if reverse else "BUY"
        elif down_sell:
            side = "BUY" if reverse else "SELL"
        if side is None or blocked:
            return out or None
        if direction == "buy" and side != "BUY":
            return out or None
        if direction == "sell" and side != "SELL":
            return out or None

        if bool(p["close_opposite"]):
            out.append(self.close(symbol, "opposite signal", "SELL" if side == "BUY" else "BUY"))
        comment = str(p["comment"])[:40] + " " + ("K" if p["signal_line"] == "main" else "D") + str(round(cur, 1))
        if side == "BUY":
            out.append(self.buy(symbol, None, None, comment, "MARKET", None, None))
        else:
            out.append(self.sell(symbol, None, None, comment, "MARKET", None, None))
        self.state[key + ":cool"] = int(p["min_bars_between_trades"])
        return out
