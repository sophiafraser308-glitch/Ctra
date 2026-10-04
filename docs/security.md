# Security

* **Authorisation** – numeric Telegram ID allow-list; roles `ADMIN` (env IDs always admin, others in DB), `OPERATOR` (view, start/pause/stop bots, close/modify positions, cancel orders, emergency stop/close; **no** account/strategy/risk/settings/user management, cannot re-enable trading or reset locks), `READ_ONLY` (view). Group chats ignored. Per-user flood throttle.
* **Secrets** – only in environment variables; OAuth tokens encrypted at rest (Fernet); masking filter on logs/audit/errors/Telegram; `.env` git-ignored; config errors never echo values.
* **Live safety** – default OFF; two independent gates (env + runtime), per-account trading permission, double confirmation for any action on LIVE accounts, demo/live mismatch refusal, kill-switch for new trading.
* **Auditing** – append-only `audit_events` (who/what/target/before/after/result/correlation id) for account, strategy, bot, risk, order, position, settings and emergency actions, plus unauthorised access attempts.
* **OAuth endpoint** – random single-use `state`, TTL, rate limit, no secrets in responses, `Cache-Control: no-store`.
* **Strategies** – see `strategy_security.md`.
* **Residual risks** – Telegram account takeover of an admin (enable Telegram 2FA); loss of `ENCRYPTION_KEY`; in-process Python sandbox limits (use Docker mode for untrusted code).
