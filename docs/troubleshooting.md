# Troubleshooting

| Symptom | Check |
|---|---|
| App exits immediately | read the first log line: config validation message (missing token/key, SQLite in production, …) |
| "Database schema missing" | `sh scripts/run_migrations.sh` (PostgreSQL) |
| Not authorised in Telegram | your numeric ID must be in `ADMIN_TELEGRAM_IDS` (use @userinfobot) |
| OAuth page says unknown/expired | request older than 15 min or already used; redirect URI in cTrader portal must equal `CTRADER_REDIRECT_URI` exactly and be public https |
| No accounts after authorising | the cTID may have no accounts of the chosen DEMO/LIVE environment |
| Account `AUTH_FAILED` | token refresh failed → re-add/re-authorise; check Connections → events |
| Bot ERROR on start | Bots → open bot → last error; typical: symbol name not available on the account, strategy dependency not allow-listed |
| Orders rejected `RISK_REJECTED_*` | Orders/Signals show the code; see `risk.md` |
| `STALE_DATA` rejections | weekend/market closed, subscription lost, or `MARKET_DATA_STALE_SECONDS` too low |
| Order `UNKNOWN` | automatic reconciliation runs; Orders → UNKNOWN → Reconcile now; never resend manually before it resolves |
| `ctrader-open-api` import error | `pip install -r requirements.txt` (needs Twisted) |
| Strategy times out | `STRATEGY_CALL_TIMEOUT_SECONDS`; heavy work belongs in bar hooks, not ticks |

## What was NOT runtime-verified (build sandbox had no network, SDK, PostgreSQL or Telegram)
Telegram flows, cTrader connection/protobuf field usage, Twisted-on-asyncio integration, PostgreSQL/Alembic, Railway/Docker builds. Verified offline: every module compiles, undefined-name scan, sandbox child process behaviour, AST validator, masking, crypto, RBAC, symbol maths, callback helpers. Run the test-suite (`pytest`) and a full DEMO session before any live use.


## STALE_MARKET_DATA on many symbols at once
* *Stale* = no price tick for `MARKET_DATA_STALE_SECONDS` (default 30). cTrader only sends a tick when the price changes, so quiet hours / demo feeds can exceed 30 s: raise it (e.g. 90-120) in `.env`.
* The bot now measures its own **event-loop lag** (System health → *Price feed*, and inside the STALE alert). Lag >= 1 s means the bot itself was busy; ~0 s means the silence comes from the broker feed.
* **Feed watchdog:** when >= 60 % of an account's symbols (min. 3) are stale while the account is connected, the spot subscription is re-sent automatically (max once per 3 min per account) and you get one alert.
* Backtests no longer compete with live trading: timeline building, simulation steps and CSV/Excel generation run in worker threads, and the strategy sandbox of a backtest runs at lower CPU priority (`nice -n 10`, or `--cpu-shares 128` in docker mode).

## Logs & Audit
Every button in **📜 Logs & Audit** sends a `.txt` file (newest first, UTC, up to 2000 rows per section); **📦 Full report** contains errors, all logs, audit trail and commands in one file.
