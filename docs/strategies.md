# Strategies

## Writing one
```python
from strategy_sdk import BaseStrategy, ema          # helpers: sma, ema, atr, rsi

STRATEGY_INFO = {                                   # must be a pure literal
    "name": "EMA Cross", "version": "1.0.0", "description": "...", "author": "me",
    "parameters": {"fast": 9, "slow": 21, "sl_pips": 20.0, "tp_pips": 40.0},
    "symbols": ["EURUSD"], "timeframes": ["M5"], "dependencies": [],
}

class EmaCross(BaseStrategy):                       # exactly one subclass
    def on_bar(self, symbol, timeframe, bar, history):
        return [self.buy(symbol, self.params["sl_pips"], self.params["tp_pips"], "signal")]
```
Hooks: `on_start`, `on_tick(symbol,bid,ask,ts_ms)`, `on_bar(symbol,timeframe,bar,history)`, `on_order_update`, `on_position_update`, `on_stop`. Signals: `self.buy/sell(symbol, sl_pips, tp_pips, comment, order_type, price)` and `self.close(symbol)`. **Strategies never choose volume** – the Risk Engine sizes the order from risk-per-trade and the SL distance. Bars are dicts `{t,o,h,l,c,v}`. Example: `strategies/ema_cross/strategy.py`.

## Upload via Telegram
Strategies → Upload → send `.py` as a document. The file is size-checked, decoded as UTF-8, **AST-validated** (nothing is executed), hashed (SHA-256) and stored in the database. A valid file becomes a new *version* (declared semver if free, otherwise patch-bumped; identical hash rejected). It is **not active** until you press Activate.

## Lifecycle
Validate (re-run checks) · Activate (previous active version becomes INACTIVE) · Deactivate · Rollback (activate an older version; audited) · Parameters (edit defaults per version; bots may override) · Statistics (trades, win rate, net P/L per version) · Delete (blocked while bots use it).
Bots are **pinned** to a version; activating a new version does not silently change running bots – change it in Bot → Settings → Strategy version (bot must be stopped).

## Sizing from a strategy
`self.buy(symbol, sl_pips, tp_pips, comment, order_type, price, volume_lots)`: pass `volume_lots=0.10` for a **fixed size** (still rounded down to the symbol step and capped by the risk profile `max_position_lots`, exposure and margin limits), or `None` to let the Risk Engine size the trade from risk-per-trade and the SL distance. `self.close(symbol, comment, side="BUY"|"SELL")` closes only that direction.

## Stochastic Cross (`strategies/stochastic_cross/strategy.py`)
MT5-style stochastic (49/5/15, Close/Close, Linear Weight). BUY when the line crosses up through `buy_level` (10); SELL when it crosses down through `sell_level` (90). Every value is a parameter (Strategies → Parameters, or Bots → Settings per bot).

## Timeframes
All cTrader periods are supported: M1 M2 M3 M4 M5 M10 M15 M30 H1 H4 H12 D1 W1 MN1. Bots can run a strategy on several symbols and several timeframes at once (the hooks receive `symbol` and `timeframe`).
