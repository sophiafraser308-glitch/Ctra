# Cycles London — 4H cycles + London 5-minute breakout (M1)

Port of the TradingView Pine v6 indicator "4H Cycles + London 5m Breakout". **Use the M1 timeframe** (bot timeframes = M1).
Upload `strategy.py` in Telegram: Strategies → Upload, then create a bot with symbol XAUUSD and timeframe M1.

## S1 — 4H cycles
* A cycle starts on the first M1 bar of every new 4H candle. Level = cycle open floored to 0.1 (4150.37 → 4150.3).
* Entry 1: cycle start + digit-sum of the integer part of the open (4150 → 10 min).
* Entry 2: cycle start + (integer part of the open mod 100) minutes (4150 → 50 min).
* At the entry bar: close > level → BUY, close < level → SELL, close == level → no trade.
* Closed on the last M1 bar of the cycle (unless TP/SL hit first). Tag `C`.

## S2 — London 5-minute breakout
* London time (DST handled in code): minutes :30–:34 of each hour between `s2_first_hour`..`s2_last_hour` give High/Low.
* From :35 until the next :30, the first M1 close above High → BUY, below Low → SELL (one trade per hour).
* Closed on the last M1 bar before the next :30. Tag `L`.

## Parameters (Strategies → Parameters, or Bots → Settings)
| name | default | meaning |
|---|---|---|
| cycle_clock | ny17 | how 4H candles are aligned: `ny17` (17:00 New York, DST aware), `utc` (00/04/08… UTC), `damascus` (00/04/08… UTC+3), `custom` |
| cycle_utc_offset_hours / cycle_anchor_hour | 0 / 0 | only for `custom`: boundaries at local hours anchor, anchor+4, … |
| s1_enabled, s1_entry1_enabled, s1_entry2_enabled | true | switches |
| s1_lots, s2_lots | 0.10 | fixed lots (when `lot_mode` = fixed) |
| s1_tp_points, s1_sl_points, s2_tp_points, s2_sl_points | 0 | platform points, 0 = off (gold: 1 point = 0.1 → Pine 7.0 = 70 points) |
| s2_enabled, s2_first_hour, s2_last_hour | true, 1, 23 | London hours (0–23) whose :30 candle is used |
| trade_direction | both | both / buy / sell |
| lot_mode | fixed | `risk` = let the Risk Engine size from the risk profile |
| comment | 4HL | prefix of the order comment |

## Checking `cycle_clock`
TradingView aligns 4H candles per data feed. Compare the "Cycle MM-dd HH:mm" cell of the indicator (chart time zone) with the cycle start used here;
if they differ pick another `cycle_clock`. The cTrader H4 candles of most brokers are UTC based, but this strategy builds cycles from M1 itself, so it follows the setting, not the broker's H4.

## Differences from the indicator
* Entries execute at market right after the signal bar closes (Pine used that bar's close) and the cycle/hour-end close executes right after the last bar closes.
* No drawings, tables or alerts; TP/SL are broker-side (SL wins in backtests when both are touched in one bar).
* Starting/restarting the bot mid-cycle: history (200 M1 bars) is replayed silently; entries whose time already passed are skipped, never traded late.
* Risk profile: with "require stop loss" ON every entry is rejected unless the SL points are set; also check max trades/day (this strategy can open up to 3 trades per 4H plus 1 per hour), max open positions and max spread.
