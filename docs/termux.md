# Termux (Android)

```bash
pkg install git
git clone <repo> app && cd app
sh scripts/termux_setup.sh      # installs build deps, venv, requirements, generates ENCRYPTION_KEY
nano .env                       # TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_IDS, CTRADER_*
termux-wake-lock
. .venv/bin/activate && python -m app
```
* Uses SQLite (`sqlite+aiosqlite:///./.runtime/app.db`) and `STRATEGY_SANDBOX_MODE=process`. Keep `APP_ENV=development|demo`.
* cTrader OAuth needs a **public https redirect URI**: expose the local `PORT` with a tunnel (cloudflared/ngrok) and use that URL for `CTRADER_REDIRECT_URI`.
* Compiling `cryptography`/`twisted` on-device can take several minutes. Android may kill background processes – disable battery optimisation for Termux and use `termux-wake-lock`. Termux is **not recommended for live trading**.
