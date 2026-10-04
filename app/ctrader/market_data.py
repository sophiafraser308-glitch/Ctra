"""Protocol helpers for spot quotes & trendbars."""
from __future__ import annotations

from typing import Any

from app.ctrader.pb import enum_name, has, opt, sdk
from app.ctrader.types import PRICE_SCALE, BarData, TickEvent

TF_TO_PERIOD = {"M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30", "H1": "H1", "H4": "H4", "D1": "D1"}
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def build_subscribe_spots(ctid: int, symbol_ids: list[int]):
    r = sdk().msgs.ProtoOASubscribeSpotsReq()
    r.ctidTraderAccountId = ctid
    r.symbolId.extend(symbol_ids)
    return r


def build_unsubscribe_spots(ctid: int, symbol_ids: list[int]):
    r = sdk().msgs.ProtoOAUnsubscribeSpotsReq()
    r.ctidTraderAccountId = ctid
    r.symbolId.extend(symbol_ids)
    return r


def parse_spot(ev: Any, now_ms: int) -> TickEvent:
    return TickEvent(ctid=int(ev.ctidTraderAccountId), symbol_id=int(ev.symbolId),
                     bid=(ev.bid / PRICE_SCALE) if has(ev, "bid") else None,
                     ask=(ev.ask / PRICE_SCALE) if has(ev, "ask") else None,
                     ts_ms=int(opt(ev, "timestamp", now_ms)) or now_ms)


def build_trendbars_req(ctid: int, symbol_id: int, tf: str, from_ms: int, to_ms: int, count: int | None = None):
    r = sdk().msgs.ProtoOAGetTrendbarsReq()
    r.ctidTraderAccountId = ctid
    r.symbolId = symbol_id
    r.period = getattr(sdk().model.ProtoOATrendbarPeriod, TF_TO_PERIOD[tf])
    r.fromTimestamp = from_ms
    r.toTimestamp = to_ms
    if count:
        r.count = count
    return r


def parse_trendbars(res: Any, tf: str) -> list[BarData]:
    out: list[BarData] = []
    for b in res.trendbar:
        low = int(b.low)
        out.append(BarData(
            symbol_id=int(res.symbolId), timeframe=tf, ts_ms=int(b.utcTimestampInMinutes) * 60_000,
            open=(low + int(opt(b, "deltaOpen", 0))) / PRICE_SCALE, high=(low + int(opt(b, "deltaHigh", 0))) / PRICE_SCALE,
            low=low / PRICE_SCALE, close=(low + int(opt(b, "deltaClose", 0))) / PRICE_SCALE,
            volume=float(opt(b, "volume", 0))))
    return out
