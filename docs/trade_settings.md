# Trade settings — lot, stop loss, take profit per timeframe

One profile, controlled **from Telegram only**, used by **live bots (demo and live accounts) and by backtests**.
Lot size / SL / TP are no longer inside the strategy file (`stochastic_cross.py` v1.1.0).

All distances are **points** (gold: 1 point = 0.1 — see `instruments.md`).

## Commands
| Command | Meaning |
|---|---|
| `/set tp 1m 50` | take profit 50 points on M1 |
| `/set sl 1m 90` | stop loss 90 points on M1 |
| `/set tp 15m 150` | take profit 150 points on M15 |
| `/set tp all 100` | default TP for every timeframe that has no value of its own |
| `/set sl all 40` | default SL |
| `/set tp 5m 0` | explicitly **no** TP on M5 (`off` works too) |
| `/set tp 5m default` | remove M5's own value → back to the default |
| `/set lot 0.1` | lot size (0 = size by the risk profile's risk %, needs a stop loss) |
| `/set multi on\|off` | one trade per timeframe (see below) |
| `/tpsl` | the panel: lot, defaults and a TP/SL table per timeframe |

Timeframes: `1m 2m 3m 4m 5m 10m 15m 30m 1h 4h 12h 1d 1w 1mn` (or `M15`, `H1` …). Arabic digits are accepted.
The same screen is available from **⚙️ Settings → 🎚 Trade settings**, the main menu (**🎚 TP / SL / Lot**) and the backtest options (**🎚 TP / SL per timeframe**).
Changing values needs the ADMIN role; viewing is open to every authorised user.

## Priority
* **Live / demo:** the timeframe's own value → the default (`/set tp all`). The strategy's own sl/tp/lots (if any) are ignored.
* **Backtest:** the timeframe's own value → the value typed in the backtest wizard (if > 0) → the default.
* Defaults on first start equal the old strategy values: lot 0.10, SL 30, TP 60.
* Changes apply from the **next signal** (running bots do not need a restart).

## One trade per timeframe (`multi on`, default)
The strategy already evaluates every selected timeframe separately. With `multi on`:
* every timeframe that meets the condition opens **its own** trade (M1, M5, M15 … each with its own TP/SL);
* an "opposite signal" / level-exit close coming from M15 closes **only the M15 trades** (before, it closed every timeframe's trades on that symbol);
* trades opened before this version have no timeframe and are closed by any timeframe's close signal.
With `multi off` the previous behaviour is kept (a close signal closes the symbol's trades of every timeframe).

Risk limits still apply per account: *max open positions* (default 3), *max symbol exposure* (default 1.5 lots) and *max total exposure* can reject the 4th/5th simultaneous timeframe trade — raise them in **🛡 Risk** if you run many timeframes.
Also *require stop loss* rejects live orders when no SL is set anywhere (the panel warns about it).

## Notifications
```
ℹ️ ORDER_FILLED
✅ XAUUSD SELL 0.1 lots filled @ 4172.01
⏱ Timeframe: M15
🎚 SL 90 · TP 150 pts

ℹ️ TRADE_CLOSED
Trade closed XAUUSD SELL 0.1 lots: net +31.80
⏱ Timeframe: M15
```
The bot's **Signals** list also shows `[M15]` next to every signal, and the order comment sent to the broker is `botname|M15|STO K9.4`.

## Backtest
Each timeframe is simulated separately with its own lot / TP / SL. The summary message, the Excel *الإعدادات* sheet and the backtest options screen list the values used per timeframe.
