# Risk engine

`RiskEngine.evaluate` is the only approval path. Order of checks (first failure wins; each returns a machine-readable code):

1. emergency `new_trading_disabled` → `RISK_REJECTED_TRADING_DISABLED`; LIVE without both gates → `RISK_REJECTED_LIVE_DISABLED`; profile/account permission → `…_TRADING_PERMISSION`
2. active locks (global/account/bot/strategy) → `RISK_REJECTED_LOCKED`
3. connection / account data → `…_NOT_CONNECTED`, `…_NO_ACCOUNT_DATA`
4. allowed symbols, UTC sessions → `…_SYMBOL_NOT_ALLOWED`, `…_SESSION`
5. fresh market data (`FRESH/STALE/NO_DATA`) and max spread → `…_STALE_DATA`, `…_SPREAD`
6. max trades/day, max open positions → `…_MAX_TRADES_PER_DAY`, `…_MAX_OPEN_POSITIONS`
7. daily loss, drawdown (peak equity), consecutive losses → create persistent lock + `…_DAILY_LOSS|DRAWDOWN|CONSECUTIVE_LOSSES`
8. position sizing: `lots = balance × risk% / (SL pips × pip value per lot)`, pip value converted to the deposit currency through live quotes (`…_NO_STOP_LOSS`, `…_NO_CONVERSION_RATE`, `…_MIN_VOLUME`); rounded **down** to the volume step, capped by profile and symbol max
9. exposure: total, per symbol, used-margin % → `…_MAX_EXPOSURE`, `…_SYMBOL_EXPOSURE`, `…_ACCOUNT_EXPOSURE`
10. free margin % → `…_FREE_MARGIN`

Counters (`risk_states`: day-start balance, peak equity, realised today, consecutive losses) and locks (`risk_locks`) are persisted; limits are also re-checked after every sync and closed trade, independent of signals. Lock policy per profile: `MANUAL` (reset required) or `NEXT_DAY` (auto-expire at 00:00 UTC). Every rejection is stored on the `signals` row (`risk_reason`, `risk_detail`). Profiles are edited in Telegram → Risk.
