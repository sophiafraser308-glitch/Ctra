from __future__ import annotations

import re
from typing import Any

# field -> (type, min, max, description)
RISK_FIELDS: dict[str, tuple[type, float | None, float | None, str]] = {
    "risk_per_trade_pct": (float, 0.01, 10.0, "Risk per trade (% of balance)"),
    "max_position_lots": (float, 0.01, 1000.0, "Max position size (lots)"),
    "max_open_positions": (int, 1, 500, "Max open positions"),
    "max_trades_per_day": (int, 1, 5000, "Max trades per day"),
    "max_daily_loss_pct": (float, 0.1, 100.0, "Max daily loss (% of day-start balance)"),
    "max_drawdown_pct": (float, 0.5, 100.0, "Max drawdown (% from peak equity)"),
    "max_consecutive_losses": (int, 1, 100, "Max consecutive losses"),
    "max_total_exposure_lots": (float, 0.01, 10000.0, "Max total exposure (lots)"),
    "max_symbol_exposure_lots": (float, 0.01, 10000.0, "Max exposure per symbol (lots)"),
    "max_account_exposure_pct": (float, 1.0, 100.0, "Max used margin (% of equity)"),
    "max_spread_pips": (float, 0.0, 1000.0, "Max spread (pips)"),
    "min_free_margin_pct": (float, 0.0, 100.0, "Min free margin (% of equity)"),
}
BOOL_FIELDS = {"require_stop_loss": "Require stop loss", "require_fresh_data": "Require fresh market data",
               "trading_enabled": "Trading permission"}
_SESSION_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


def parse_risk_value(field: str, raw: str) -> Any:
    from app.core.exceptions import ValidationFailed
    raw = raw.strip()
    if field in RISK_FIELDS:
        typ, lo, hi, _ = RISK_FIELDS[field]
        try:
            val = typ(raw) if typ is float else int(float(raw))
        except ValueError as exc:
            raise ValidationFailed(f"Expected a number for {field}") from exc
        if (lo is not None and val < lo) or (hi is not None and val > hi):
            raise ValidationFailed(f"{field} must be between {lo} and {hi}")
        return val
    if field == "allowed_symbols":
        if raw.lower() in ("", "all", "*", "-"):
            return []
        syms = [s.strip().upper() for s in raw.replace(";", ",").split(",") if s.strip()]
        if any(not re.fullmatch(r"[A-Z0-9_.#/-]{2,32}", s) for s in syms):
            raise ValidationFailed("Invalid symbol list")
        return syms
    if field == "allowed_sessions":
        if raw.lower() in ("", "all", "*", "-"):
            return []
        parts = [s.strip() for s in raw.replace(";", ",").split(",") if s.strip()]
        if any(not _SESSION_RE.match(p) for p in parts):
            raise ValidationFailed("Sessions must look like 07:00-16:00 (UTC), comma separated")
        return parts
    if field == "lock_policy":
        v = raw.upper()
        if v not in ("MANUAL", "NEXT_DAY"):
            raise ValidationFailed("lock_policy must be MANUAL or NEXT_DAY")
        return v
    raise ValidationFailed(f"Unknown risk field {field}")
