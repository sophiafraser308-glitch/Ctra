# Installation

## Requirements
Python 3.11+ (3.12 recommended), PostgreSQL 14+ (production) or SQLite (dev/Termux), a Telegram bot token, a cTrader Open API application.

## Steps
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/generate_key.py          # paste into ENCRYPTION_KEY
# edit .env: TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_IDS, DATABASE_URL, CTRADER_*
sh scripts/run_migrations.sh            # PostgreSQL only; SQLite creates tables automatically
python -m app
```
On start the app validates configuration and aborts with a clear (secret-free) message if anything is wrong.

## First run
1. Send `/start` to your bot from an ID listed in `ADMIN_TELEGRAM_IDS`.
2. Accounts → Add Account (see `authentication.md`).
3. Strategies → Upload strategy (see `strategies.md`) → Activate.
4. Bots → Create bot → Start (DEMO first).
