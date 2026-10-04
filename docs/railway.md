# Railway

1. Create a project, add the **PostgreSQL** plugin, and a service from this repo (uses `Dockerfile`; `railway.json` configures health check `/healthz`, restart on failure, **1 replica**).
2. Variables: `APP_ENV`, `TELEGRAM_BOT_TOKEN`, `ADMIN_TELEGRAM_IDS`, `ENCRYPTION_KEY`, `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `CTRADER_REDIRECT_URI`, `DATABASE_URL=${{Postgres.DATABASE_URL}}`. Railway injects `PORT` (the app listens on it).
3. Generate a public domain for the service and register `https://<domain>/auth/ctrader/callback` as the redirect URI in the cTrader portal (identical string in `CTRADER_REDIRECT_URI`).
4. Deploy. Migrations run automatically in `scripts/entrypoint.sh`.

Notes: the filesystem is ephemeral – safe, because strategy sources live in the DB and `strategies/_store` is only a cache. Docker sandbox mode is unavailable on Railway; use the default `process` mode and restrict strategy upload to admins. **Not tested on Railway in the build environment.**
