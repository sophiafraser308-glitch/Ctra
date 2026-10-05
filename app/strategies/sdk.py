"""Strategy SDK. Strategies do:  from strategy_sdk import BaseStrategy

The sandbox exposes this module to strategies under the name `strategy_sdk`.
Pure Python, stdlib only (it is imported inside the restricted child process).
"""
from __future__ import annotations

from typing import Any


class BaseStrategy:
    """Subclass and override the hooks. All hooks may return a list of signals (or None)."""

    def __init__(self, params: dict[str, Any], context: dict[str, Any]) -> None:
        self.params = dict(params)
        self.context = dict(context)     # {"symbols": [...], "timeframes": [...], "environment": "DEMO"|"LIVE"}
        self.state: dict[str, Any] = {}

    # ---- hooks -------------------------------------------------------------
    def on_start(self) -> list[dict] | None:
        return None

    def on_tick(self, symbol: str, bid: float | None, ask: float | None, ts_ms: int) -> list[dict] | None:
        return None

    def on_bar(self, symbol: str, timeframe: str, bar: dict, history: list[dict]) -> list[dict] | None:
        """bar/history items: {"t": ms, "o":..., "h":..., "l":..., "c":..., "v":...}; history excludes `bar`."""
        return None

    def on_order_update(self, update: dict) -> None:
        return None

    def on_position_update(self, update: dict) -> None:
        return None

    def on_stop(self) -> None:
        return None

    # ---- helpers ---------------------------------------------------------------
    @staticmethod
    def buy(symbol: str, stop_loss_pips: float | None = None, take_profit_pips: float | None = None,
            comment: str | None = None, order_type: str = "MARKET", price: float | None = None,
            volume_lots: float | None = None) -> dict:
        """volume_lots=None -> the Risk Engine sizes the trade from risk-per-trade; a number -> fixed lots (still capped by risk limits)."""
        return _signal(symbol, "BUY", stop_loss_pips, take_profit_pips, comment, order_type, price, volume_lots)

    @staticmethod
    def sell(symbol: str, stop_loss_pips: float | None = None, take_profit_pips: float | None = None,
             comment: str | None = None, order_type: str = "MARKET", price: float | None = None,
             volume_lots: float | None = None) -> dict:
        return _signal(symbol, "SELL", stop_loss_pips, take_profit_pips, comment, order_type, price, volume_lots)

    @staticmethod
    def close(symbol: str, comment: str | None = None, side: str | None = None) -> dict:
        """Close this bot's positions on `symbol`; side='BUY'/'SELL' closes only that direction."""
        d = _signal(symbol, "CLOSE", None, None, comment, "MARKET", None, None)
        if side:
            d["close_side"] = side
        return d


def _signal(symbol, side, sl, tp, comment, order_type, price, volume_lots=None) -> dict:
    d: dict[str, Any] = {"symbol": symbol, "side": side, "order_type": order_type}
    if volume_lots is not None:
        d["volume_lots"] = float(volume_lots)
    if sl is not None:
        d["stop_loss_pips"] = float(sl)
    if tp is not None:
        d["take_profit_pips"] = float(tp)
    if comment:
        d["comment"] = str(comment)[:64]
    if price is not None:
        d["price"] = float(price)
    return d


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def ema(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def atr(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(len(values) - n, len(values)):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)
