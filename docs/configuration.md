# Configuration (environment variables)

See `.env.example` for the full annotated list. Highlights:

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | development | `development`/`demo`/`production`. Production requires PostgreSQL. |
| `TELEGRAM_BOT_TOKEN` | – | required |
| `ADMIN_TELEGRAM_IDS` | – | comma separated numeric IDs; always ADMIN |
| `DATABASE_URL` | sqlite | `postgres://`/`postgresql://` auto-converted to asyncpg |
| `ENCRYPTION_KEY` | – | Fernet key (`scripts/generate_key.py`); losing it makes stored tokens unreadable |
| `CTRADER_CLIENT_ID/SECRET/REDIRECT_URI` | – | from the Spotware Open API portal |
| `CTRADER_SCOPE` | trading | `accounts` = view only |
| `LIVE_TRADING_ENABLED` | **false** | hard gate; only honoured with `APP_ENV=production` |
| `PORT` / `WEB_HOST` | 8080 / 0.0.0.0 | OAuth callback + `/healthz` |
| `STRATEGY_SANDBOX_MODE` | process | `docker` = strongest isolation (needs docker CLI + image) |
| `STRATEGY_ALLOWED_IMPORTS` | stdlib subset | allow-list for strategy imports |
| `MARKET_DATA_STALE_SECONDS` | 30 | quote older than this ⇒ STALE ⇒ risk rejects |
| `RECONCILE_INTERVAL_SECONDS` | 120 | periodic reconciliation |
| `NOTIFICATION_THROTTLE_SECONDS` | 300 | per event-key throttling |

Runtime switches (stored in DB, changed from Telegram): `live_trading_enabled`, `new_trading_disabled`, notification mutes. **Live orders require both** the env gate and the runtime switch.
