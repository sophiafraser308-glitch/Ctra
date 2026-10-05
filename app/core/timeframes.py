"""Timeframe definitions shared by live trading, bots, strategies and backtests (names match cTrader trendbar periods)."""
from __future__ import annotations

from datetime import datetime, timezone

TF_ORDER = ["M1", "M2", "M3", "M4", "M5", "M10", "M15", "M30", "H1", "H4", "H12", "D1", "W1", "MN1"]
TIMEFRAMES = tuple(TF_ORDER)
TF_SECONDS = {"M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M10": 600, "M15": 900, "M30": 1800,
              "H1": 3600, "H4": 14400, "H12": 43200, "D1": 86400, "W1": 604800, "MN1": 2592000}
TF_LABEL = {"M1": "1 min", "M2": "2 min", "M3": "3 min", "M4": "4 min", "M5": "5 min", "M10": "10 min", "M15": "15 min",
            "M30": "30 min", "H1": "1 hour", "H4": "4 hours", "H12": "12 hours", "D1": "Daily", "W1": "Weekly", "MN1": "Monthly"}
# conservative per-request history window (days) for cTrader trendbar requests
TF_CHUNK_DAYS = {"M1": 3, "M2": 6, "M3": 14, "M4": 14, "M5": 30, "M10": 60, "M15": 90, "M30": 150, "H1": 300,
                 "H4": 730, "H12": 1095, "D1": 1825, "W1": 3650, "MN1": 7300}
_WEEK_MS = 7 * 86_400_000
_MONDAY_SHIFT = 3 * 86_400_000          # 1970-01-01 was a Thursday


def bucket_start_ms(tf: str, ms: int) -> int:
    """Open time (UTC ms) of the bar containing `ms`."""
    if tf == "W1":
        return ((ms + _MONDAY_SHIFT) // _WEEK_MS) * _WEEK_MS - _MONDAY_SHIFT
    if tf == "MN1":
        d = datetime.fromtimestamp(ms / 1000, timezone.utc)
        return int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000)
    span = TF_SECONDS[tf] * 1000
    return ms // span * span
