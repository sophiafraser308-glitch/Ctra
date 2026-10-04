# Bots

State machine (`app/bots/manager.py`): `CREATED → STARTING → RUNNING ⇄ PAUSED`, `RUNNING/PAUSED → LOCKED` (risk lock), `→ STOPPING → STOPPED`, failures `ERROR` (could not start) / `CRASHED` (died while running), `RESTARTING`. Illegal transitions raise `ConflictError`.

* **Idempotent commands** – starting a running bot, stopping a stopped bot etc. return "already …"; a per-bot `asyncio.Lock` serialises transitions; Telegram button presses are de-duplicated by callback id in the `commands` table.
* **Runner** (`app/bots/runner.py`) – one task per bot: coalesced ticks (only the latest tick per symbol is delivered), bar-close events with history, execution events; each strategy call has a timeout.
* **Safety gates at start** – LIVE bots need env + runtime live switch; new-trading kill-switch blocks start; active locks put the bot straight into `LOCKED`.
* **Locks** – daily loss / drawdown / consecutive losses / crash loop lock the account or bot; locked bots keep running but their signals are dropped and orders refused until a lock is released (or expires for `NEXT_DAY` policy).
* **Restart recovery** – `desired_state` is persisted; on startup bots that should run are started again *after* reconciliation. Graceful shutdown keeps `desired_state`.
* Configuration (symbols, timeframes, parameter overrides, strategy version, risk profile) can only be edited while stopped.
