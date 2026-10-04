# Deployment

Run **exactly one instance** (Telegram long polling + broker sessions are single-owner).

## Docker Compose (VPS)
```bash
cp .env.example .env     # fill TELEGRAM_*, ENCRYPTION_KEY, CTRADER_*, POSTGRES_PASSWORD, APP_ENV=demo|production
docker compose up -d --build
```
The entrypoint runs `alembic upgrade head` for non-SQLite URLs, then `python -m app`. Put a TLS reverse proxy (Caddy/nginx) in front of `PORT` so `CTRADER_REDIRECT_URI` is a public **https** URL.

## Live trading checklist
`APP_ENV=production`, PostgreSQL, `LIVE_TRADING_ENABLED=true`, then Telegram → Settings → Enable LIVE (double confirm), account trading permission ON, risk profile reviewed, DEMO validated first.

## Backups
Back up PostgreSQL and **`ENCRYPTION_KEY`** separately. Without the key stored tokens cannot be decrypted (accounts must be re-authorised).

## Upgrades
`git pull && docker compose up -d --build`. New schema changes ship as Alembic revisions.
