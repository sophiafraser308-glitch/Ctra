"""Instrument definitions: what a "point" is, decimals shown, and value per point — ONE source of truth used by live
orders (SL/TP distances), the Risk Engine (pip value), spreads, and backtests.

Definitions (operator-defined):
  GOLD   : 1 point = 0.1 price units, prices shown WITHOUT decimals.   4150 -> 4152 = 20 points.   (100 oz lot: 1 point = $10 / lot)
  OIL    : 1 point = 0.01, 2 decimals shown.                                                       (1000 bbl lot: 1 point = $10 / lot)
  FOREX  : 1 point = 1 pip = 0.0001 (JPY pairs 0.01), 5 decimals shown (JPY 3).
           value per point per 1 lot = 10 units of the QUOTE currency (JPY pairs: 1000 JPY) converted to the account currency.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.market_data.catalog import classify


@dataclass(frozen=True)
class InstrumentDef:
    category: str            # GOLD | OIL | FOREX
    point_size: float        # price units per point
    display_decimals: int    # decimals shown in Telegram / Excel (the broker may quote more)
    note: str


GOLD = InstrumentDef("GOLD", 0.1, 0, "1 point = 0.1 · 4150→4152 = 20 points · no decimals shown")
OIL = InstrumentDef("OIL", 0.01, 2, "1 point = 0.01")
FOREX = InstrumentDef("FOREX", 0.0001, 5, "1 point = 1 pip = 0.0001")
FOREX_JPY = InstrumentDef("FOREX", 0.01, 3, "1 point = 1 pip = 0.01")


def definition_for(name: str) -> InstrumentDef | None:
    c = classify(name)
    if c is None:
        return None
    if c.category == "GOLD":
        return GOLD
    if c.category == "OIL":
        return OIL
    return FOREX_JPY if c.base.endswith("JPY") else FOREX


def apply_to_symbol(info) -> None:
    """Attach the operator's point/decimals definition to a SymbolInfo (no-op for unknown instruments)."""
    d = definition_for(info.name)
    if d is not None:
        info.point_size = d.point_size
        info.display_decimals = d.display_decimals


def value_per_point(lot_units: float, point_size: float, quote_to_deposit_rate: float) -> float:
    """Money value (account currency) of a 1-point move for 1.0 lot."""
    return lot_units * point_size * quote_to_deposit_rate


# static reference (values in QUOTE currency; valid as USD for USD-quoted instruments)
STATIC_TABLE = [
    ("XAUUSD / GOLD", GOLD, 100, "USD"), ("XTIUSD / USOIL", OIL, 1000, "USD"), ("XBRUSD / UKOIL", OIL, 1000, "USD"),
    ("EURUSD", FOREX, 100_000, "USD"), ("GBPUSD", FOREX, 100_000, "USD"), ("AUDUSD", FOREX, 100_000, "USD"), ("NZDUSD", FOREX, 100_000, "USD"),
    ("USDCHF", FOREX, 100_000, "CHF"), ("USDCAD", FOREX, 100_000, "CAD"), ("USDJPY", FOREX_JPY, 100_000, "JPY"),
]
