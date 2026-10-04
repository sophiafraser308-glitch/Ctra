#!/usr/bin/env sh
# Apply database migrations (PostgreSQL in production). Requires DATABASE_URL.
set -eu
cd "$(dirname "$0")/.."
exec alembic upgrade head
