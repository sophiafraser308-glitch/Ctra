from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_id() -> str:
    """12 hex chars: short enough for Telegram callback_data (64 byte limit)."""
    return uuid.uuid4().hex[:12]


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def new_token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def next_utc_midnight(now: datetime | None = None) -> datetime:
    now = now or utcnow()
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def utc_day_start(now: datetime | None = None) -> datetime:
    now = now or utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def fmt_dt(dt: datetime | None) -> str:
    dt = ensure_aware(dt)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"


def fmt_ago(dt: datetime | None, now: datetime | None = None) -> str:
    dt = ensure_aware(dt)
    if not dt:
        return "never"
    secs = max(0, int(((now or utcnow()) - dt).total_seconds()))
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def fmt_money(v: float | None, cur: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:,.2f} {cur}".strip()


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
