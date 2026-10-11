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
