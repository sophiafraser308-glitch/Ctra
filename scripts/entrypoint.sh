#!/usr/bin/env sh
# Container entrypoint: migrate (non-SQLite) then start the application.
set -eu
cd /app
case "${DATABASE_URL:-}" in
  sqlite*|"") ;;
  *) alembic upgrade head ;;
esac
exec python -m app
