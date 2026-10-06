# cTrader Telegram Trading Platform

Autonomous, Telegram-controlled trading management platform for the **cTrader Open API**: multi-account (explicit DEMO/LIVE), OAuth2, strategy plugins uploaded from Telegram (AST-validated, versioned, sandboxed), bot manager, Risk Engine with persistent locks, idempotent Order Manager with UNKNOWN-order handling, reconciliation, watchdog, audit and throttled notifications. PostgreSQL + SQLAlchemy + Alembic. Docker / Railway / Termux ready.

> **Live trading is OFF by default** and needs two independent switches plus double confirmation. Start on DEMO.

## Quick start
```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env && python scripts/generate_key.py   # put key in ENCRYPTION_KEY, fill the rest
sh scripts/run_migrations.sh        # PostgreSQL (SQLite auto-creates)
python -m app
```
Then `/start` in Telegram → Accounts → Add Account → Strategies → Upload (`strategies/ema_cross/strategy.py`) → Activate → Bots → Create → Start.

## Backtest
Telegram → 🧪 Backtest: multi-symbol, any timeframe M1→MN1, date range, own options, summary message + Excel/CSV of every trade. See `docs/backtest.md`.

## Layout
`app/` source · `strategies/` examples + cache · `tests/` · `migrations/` (Alembic) · `scripts/` · `docs/` (22 guides) · `Dockerfile`, `docker-compose.yml`, `railway.json`.

## Docs
architecture · installation · configuration · telegram · ctrader · authentication · accounts · strategies · strategy_security · bots · risk · orders · positions · reconciliation · watchdog · deployment · railway · termux · security · troubleshooting · backtest · instruments (all in `docs/`).

## Verification status
Written without network access: code compiles and the offline-testable parts are verified, but Telegram, cTrader, PostgreSQL, Twisted-on-asyncio and cloud deployment were **not** runtime-tested. See `docs/troubleshooting.md` ("What was NOT runtime-verified") and run `pytest` + a DEMO session first. Trading involves risk; no profitability is implied.


## Trade settings (lot · TP · SL per timeframe)
Controlled from Telegram for live, demo and backtest: `/set tp 15m 150`, `/set sl 15m 90`, `/set lot 0.1`, `/tpsl`. See [docs/trade_settings.md](docs/trade_settings.md).
