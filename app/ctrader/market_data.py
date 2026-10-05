"""Protocol helpers for spot quotes & trendbars."""
from __future__ import annotations

from typing import Any

from app.ctrader.pb import enum_name, has, opt, sdk
from app.ctrader.types import PRICE_SCALE, BarData, TickEvent

from app.core.timeframes import TF_ORDER, TF_SECONDS  # noqa: E402,F401

TF_TO_PERIOD = {tf: tf for tf in TF_ORDER}   # enum names are identical (M1..M30, H1, H4, H12, D1, W1, MN1)


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


def trendbars_has_more(res: Any) -> bool:
    return bool(opt(res, "hasMore", False))


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
