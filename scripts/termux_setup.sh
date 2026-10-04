#!/data/data/com.termux/files/usr/bin/sh
# Termux setup (Android). SQLite + process sandbox mode; no Docker/PostgreSQL required.
set -eu
pkg update -y
pkg install -y python git rust binutils libffi openssl build-essential
cd "$(dirname "$0")/.."
python -m venv .venv
. .venv/bin/activate
pip install --upgrade pip wheel
# Twisted/cryptography compile from source on Termux; this can take several minutes.
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
if ! grep -q '^ENCRYPTION_KEY=.\+' .env; then
  KEY="$(python scripts/generate_key.py)"
  sed -i "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=${KEY}|" .env
  echo "Generated ENCRYPTION_KEY into .env"
fi
mkdir -p .runtime
echo "Edit .env (TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_IDS, CTRADER_*), then run:"
echo "  termux-wake-lock && . .venv/bin/activate && python -m app"
echo "Note: cTrader OAuth needs a PUBLIC https redirect URI (e.g. a tunnel such as cloudflared / ngrok) pointing to this device's WEB port."
