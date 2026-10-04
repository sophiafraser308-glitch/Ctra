"""Example strategy: EMA crossover on closed bars. Upload this file via Telegram (Strategies -> Upload)."""
from strategy_sdk import BaseStrategy, ema

STRATEGY_INFO = {
    "name": "EMA Cross",
    "version": "1.0.0",
    "description": "Buy when fast EMA crosses above slow EMA, sell on the opposite cross. Fixed SL/TP in pips.",
    "author": "platform-example",
    "parameters": {"fast": 9, "slow": 21, "sl_pips": 20.0, "tp_pips": 40.0},
    "symbols": ["EURUSD"],
    "timeframes": ["M5"],
    "dependencies": [],
}


class EmaCross(BaseStrategy):
    def on_bar(self, symbol, timeframe, bar, history):
        closes = [b["c"] for b in history] + [bar["c"]]
        fast_n, slow_n = int(self.params["fast"]), int(self.params["slow"])
        if len(closes) < slow_n + 2:
            return None
        f_now, s_now = ema(closes, fast_n), ema(closes, slow_n)
        f_prev, s_prev = ema(closes[:-1], fast_n), ema(closes[:-1], slow_n)
        if None in (f_now, s_now, f_prev, s_prev):
            return None
        key = symbol + timeframe
        last = self.state.get(key)
        if f_prev <= s_prev and f_now > s_now and last != "BUY":
            self.state[key] = "BUY"
            return [self.close(symbol, "reverse"), self.buy(symbol, self.params["sl_pips"], self.params["tp_pips"], "ema cross up")] if last == "SELL" else [self.buy(symbol, self.params["sl_pips"], self.params["tp_pips"], "ema cross up")]
        if f_prev >= s_prev and f_now < s_now and last != "SELL":
            self.state[key] = "SELL"
            return [self.close(symbol, "reverse"), self.sell(symbol, self.params["sl_pips"], self.params["tp_pips"], "ema cross down")] if last == "BUY" else [self.sell(symbol, self.params["sl_pips"], self.params["tp_pips"], "ema cross down")]
        return None
