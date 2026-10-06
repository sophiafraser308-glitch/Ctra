"""Trade settings controlled from Telegram — ONE profile shared by live/demo bots and by backtests.

Everything is in POINTS (gold 1 point = 0.1, forex 1 point = 1 pip ... see market_data/instruments.py).

Stored shape (JSON in SystemSetting 'trade_settings'):
    {"lot_size": 0.10,                 # 0 = let the Risk Engine size the trade from risk-per-trade (needs a stop loss)
     "sl_default": 30.0, "tp_default": 60.0,   # used by every timeframe without its own value; 0 = no SL / no TP
     "sl": {"M1": 90.0}, "tp": {"M1": 50.0, "M15": 150.0},   # per-timeframe overrides; 0 = explicitly none
     "multi_tf": True}                 # True = every timeframe that meets the strategy condition opens/closes its own trade

Pure functions only (no I/O) so they are unit-tested and shared by the live pipeline and the backtest service.
"""
from __future__ import annotations

import html
import re
from typing import Any

from app.core.exceptions import ValidationFailed
from app.core.timeframes import TF_ORDER, TIMEFRAMES

KEY = "trade_settings"
MAX_POINTS = 100_000.0
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

DEFAULTS: dict[str, Any] = {"lot_size": 0.10, "sl_default": 30.0, "tp_default": 60.0, "sl": {}, "tp": {}, "multi_tf": True}

_TF_RE = re.compile(r"^(\d{1,3})?(MIN|MN|M|H|D|W)(\d{1,3})?$")


# ---- parsing -------------------------------------------------------------------------------------
def clean_number(text: str) -> str:
    return (text or "").strip().translate(AR_DIGITS).replace(",", ".").replace("٫", ".")


def parse_tf(text: str) -> str:
    """'1m' -> M1, '15m' -> M15, '1h' -> H1, '4h' -> H4, '1d' -> D1, '1w' -> W1, '1mn' -> MN1, 'M15'/'H4' accepted as is."""
    t = clean_number(text).upper().replace(" ", "")
    if t in TIMEFRAMES:
        return t
    m = _TF_RE.match(t)
    if m:
        a, unit, b = m.groups()
        if (a is None) != (b is None) or (a is None and b is None and unit in ("D", "W")):
            n = a or b or "1"
            unit = "M" if unit == "MIN" else unit
            cand = f"{unit}{n}"
            if cand in TIMEFRAMES:
                return cand
    raise ValidationFailed(f"Unknown timeframe '{text}'. Examples: 1m 5m 15m 30m 1h 4h 1d 1w  (all: {', '.join(TF_ORDER)})")


def parse_points(text: str) -> float:
    """Number of points; '0' / 'off' / 'none' = disabled (0.0)."""
    raw = clean_number(text).lower()
    if raw in ("off", "none", "no", "-", "disable", "disabled"):
        return 0.0
    try:
        v = float(raw)
    except ValueError:
        raise ValidationFailed("Send a number of points (e.g. 150), or 0 / off to disable") from None
    if v != v or v < 0 or v > MAX_POINTS:
        raise ValidationFailed(f"Points must be between 0 and {MAX_POINTS:g}")
    return v


def parse_lot(text: str) -> float:
    try:
        v = float(clean_number(text))
    except ValueError:
        raise ValidationFailed("Send the lot size, e.g. 0.10 (0 = size by risk %)") from None
    if v != v or v < 0 or v > 1000:
        raise ValidationFailed("Lot size must be between 0 and 1000")
    return round(v, 4)


def parse_bool(text: str) -> bool:
    raw = clean_number(text).lower()
    if raw in ("on", "1", "yes", "true", "enable", "enabled"):
        return True
    if raw in ("off", "0", "no", "false", "disable", "disabled"):
        return False
    raise ValidationFailed("Use on or off")


# ---- model -----------------------------------------------------------------------------------------
def normalize(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Merge with defaults and drop anything invalid (a damaged DB value must never break trading)."""
    out: dict[str, Any] = {"lot_size": DEFAULTS["lot_size"], "sl_default": DEFAULTS["sl_default"], "tp_default": DEFAULTS["tp_default"],
                           "sl": {}, "tp": {}, "multi_tf": DEFAULTS["multi_tf"]}
    raw = raw if isinstance(raw, dict) else {}
    for k in ("lot_size", "sl_default", "tp_default"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= (1000 if k == "lot_size" else MAX_POINTS):
            out[k] = float(v)
    for kind in ("sl", "tp"):
        per = raw.get(kind)
        if isinstance(per, dict):
            out[kind] = {tf: float(v) for tf, v in per.items()
                         if tf in TIMEFRAMES and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= MAX_POINTS}
    if isinstance(raw.get("multi_tf"), bool):
        out["multi_tf"] = raw["multi_tf"]
    return out


def with_value(ts: dict[str, Any], kind: str, tf: str | None, value: float | None) -> dict[str, Any]:
    """Return a new settings dict. kind: tp | sl ; tf=None -> the default for all timeframes ; value=None -> remove the tf override."""
    if kind not in ("tp", "sl"):
        raise ValidationFailed("Use tp or sl")
    new = normalize(ts)
    if tf is None:
        if value is None:
            raise ValidationFailed("The default for all timeframes cannot be removed; set 0 to disable it")
        new[f"{kind}_default"] = float(value)
    elif value is None:
        new[kind].pop(tf, None)
    else:
        new[kind][tf] = float(value)
    return new


def effective(ts: dict[str, Any], tf: str | None) -> dict[str, Any]:
    """Final values for one timeframe. sl/tp are None when disabled (0). *_src says where the value came from."""
    ts = normalize(ts)
    out: dict[str, Any] = {"lot": ts["lot_size"] or None}
    for kind in ("sl", "tp"):
        if tf and tf in ts[kind]:
            v, src = ts[kind][tf], "tf"
        else:
            v, src = ts[f"{kind}_default"], "default"
        out[kind], out[f"{kind}_src"] = (v if v > 0 else None), src
    return out


def backtest_levels(ts: dict[str, Any], tf: str, sl_opt: float = 0.0, tp_opt: float = 0.0, lot_opt: float = 0.0) -> dict[str, Any]:
    """Final lot/SL/TP of one backtest timeframe. Priority: the timeframe's own value > the wizard's run option (>0) > the default.
    Returns sl/tp as points (0.0 = none) and lot (0.0 = the engine's default size)."""
    ts = normalize(ts)
    sl = ts["sl"][tf] if tf in ts["sl"] else (float(sl_opt) if sl_opt and sl_opt > 0 else ts["sl_default"])
    tp = ts["tp"][tf] if tf in ts["tp"] else (float(tp_opt) if tp_opt and tp_opt > 0 else ts["tp_default"])
    lot = float(lot_opt) if lot_opt and lot_opt > 0 else ts["lot_size"]
    return {"sl": sl, "tp": tp, "lot": lot}


# ---- display -------------------------------------------------------------------------------------------
def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:g}"


def panel_text(ts: dict[str, Any], tfs_in_use: list[str] | None = None) -> str:
    """Telegram HTML panel: lot, defaults and a TP/SL table per timeframe ('*' = inherited from the default)."""
    ts = normalize(ts)
    show = [tf for tf in TF_ORDER if tf in set(tfs_in_use or []) | set(ts["sl"]) | set(ts["tp"])]
    lines = ["🎚 <b>Trade settings</b>  <i>(live · demo · backtest — points)</i>", "",
             f"📦 Lot size: <b>{ts['lot_size']:g}</b>" + ("  (0 = by risk %)" if ts["lot_size"] == 0 else ""),
             f"🎯 Default TP: <b>{_fmt(ts['tp_default'] or None)}</b> · 🛑 Default SL: <b>{_fmt(ts['sl_default'] or None)}</b>",
             f"🧩 One trade per timeframe: <b>{'ON' if ts['multi_tf'] else 'OFF'}</b>", ""]
    if show:
        rows = ["TF    TP       SL"]
        for tf in show:
            e = effective(ts, tf)
            tp = _fmt(e["tp"]) + ("*" if e["tp_src"] == "default" else "")
            sl = _fmt(e["sl"]) + ("*" if e["sl_src"] == "default" else "")
            rows.append(f"{tf:<5} {tp:<8} {sl}")
        lines += ["<pre>" + html.escape("\n".join(rows)) + "</pre>", "<i>* = from the default</i>"]
    else:
        lines.append("No per-timeframe values yet. Pick a timeframe below or send e.g. <code>/set tp 15m 150</code>")
    if not ts["sl_default"] and not ts["sl"]:
        lines.append("⚠️ No stop loss anywhere: a risk profile with 'require stop loss' will reject live orders.")
    return "\n".join(lines)


HELP = ("<b>Commands</b>\n"
        "<code>/set tp 1m 50</code> — take profit 50 points on M1\n"
        "<code>/set sl 1m 90</code> — stop loss 90 points on M1\n"
        "<code>/set tp 15m 150</code>\n"
        "<code>/set tp all 100</code> — default for every timeframe without its own value\n"
        "<code>/set tp 5m 0</code> — no TP on M5 · <code>/set tp 5m default</code> — back to the default\n"
        "<code>/set lot 0.1</code> — lot size (0 = by risk %)\n"
        "<code>/set multi on|off</code> — one trade per timeframe\n"
        "<code>/tpsl</code> — show the panel\n"
        "Timeframes: 1m 5m 15m 30m 1h 4h 1d 1w (or M15, H1 ...)")
