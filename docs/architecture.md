# Architecture

Single asyncio process (`python -m app`) composed of explicit services wired in `app/core/container.py` (`AppContext`, no globals).

```
Telegram (aiogram, long polling) ──► handlers ──► services ─┬─► PostgreSQL / SQLite (SQLAlchemy async)
aiohttp (/healthz, OAuth callback) ─► AccountService         ├─► CTraderGateway ─► ConnectionManager(DEMO|LIVE) ─► Spotware Open API
                                                              └─► sandboxed strategy processes (JSON lines over stdio)
Strategy signal ─► SignalPipeline ─► RiskEngine ─► OrderManager ─► CTraderGateway
broker events ─► OrderManager.handle_execution ─► PositionService ─► RiskEngine (locks)
```

## Layers
| Package | Responsibility |
|---|---|
| `app/config` | pydantic-settings, startup validation (fail fast, no secrets in messages) |
| `app/security` | Fernet crypto, secret masking, RBAC, rate limiting, Telegram user allow-list |
| `app/database`, `app/models` | async engine, 24 entities (users, accounts, credentials, strategies, bots, signals, orders, positions, trades, locks, risk state, recon events, logs, notifications, commands, audit, workers, heartbeats, settings) |
| `app/ctrader` | **only** place that touches protobuf: OAuth, connection manager, gateway facade |
| `app/accounts`, `app/market_data`, `app/risk`, `app/orders`, `app/positions`, `app/reconciliation` | trading core |
| `app/strategies` | SDK, AST validator, service (versioning), sandbox runner + host |
| `app/bots` | bot state machine, per-bot runner, signal pipeline |
| `app/watchdog`, `app/workers`, `app/notifications`, `app/audit`, `app/logging` | operations |
| `app/bot` | Telegram UI: middlewares, keyboards, FSM states, handlers |
| `app/web` | `/healthz` and cTrader OAuth redirect endpoint |

## Startup sequence (`app/__main__.py`)
1 install Twisted asyncio reactor → 2 logging + masking → 3 config validation → 4 DB (+schema check) → 5 services → 6 heartbeat/notifier → 7 restore strategy cache from DB → 8 web server → 9 connect enabled accounts → 10 **reconcile before any bot trades** → 11 bot restart recovery → 12 watchdog → Telegram polling.

## Shutdown
Stop polling → watchdog/recon → stop bot runners (DB `desired_state` kept, so they auto-recover) → notify → gateway logout/close → web → heartbeat → DB close. Pending orders are never cancelled implicitly.

## Persistence rule
Critical state (orders, risk counters, locks, bot desired state, strategy sources, tokens) lives in the database. The filesystem (`strategies/_store`) is only a cache; Railway's ephemeral disk is therefore safe.
