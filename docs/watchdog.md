# Watchdog & heartbeat

`HeartbeatService` collects per-component status (application, database, telegram, ctrader, market_data, order_manager, reconciliation, watchdog, bots, `bot:<id>`) and upserts `heartbeats`/`workers` every `HEARTBEAT_INTERVAL_SECONDS`; shown in Telegram → System Health.

`WatchdogService` (every `WATCHDOG_INTERVAL_SECONDS`) checks: database ping · Telegram `get_me` · cTrader managers (restart if down > 3 min) and account sessions (reconnect with exponential backoff) · bot runners/strategy processes (crash ⇒ restart with backoff; **crash loop** ⇒ stop + BOT lock + notification) · stuck `PROCESSING` orders ⇒ UNKNOWN + reconcile · overdue reconciliation · ERROR-burst detection ⇒ safety reconciliation · periodic token refresh.

Safe-recovery rules: the watchdog never resends orders, never clears locks, and never starts bots that were stopped by the user (`desired_state`).
