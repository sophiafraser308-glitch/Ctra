"""Protocol helpers for positions & deals."""
from __future__ import annotations

from typing import Any

from app.ctrader.pb import enum_name, has, opt, sdk
from app.ctrader.types import PRICE_SCALE, BrokerDeal, BrokerPosition


def side_name(v: int) -> str:
    return enum_name(sdk().model.ProtoOATradeSide, v)  # BUY | SELL


def parse_position(p: Any) -> BrokerPosition:
    td = p.tradeData
    digits = int(opt(p, "moneyDigits", 2))
    scale = 10 ** digits
    return BrokerPosition(
        position_id=int(p.positionId), symbol_id=int(td.symbolId), side=side_name(td.tradeSide),
        volume=int(td.volume), entry_price=opt(p, "price"),
        stop_loss=opt(p, "stopLoss"), take_profit=opt(p, "takeProfit"),
        swap=int(opt(p, "swap", 0)) / scale, commission=int(opt(p, "commission", 0)) / scale,
        used_margin=int(opt(p, "usedMargin", 0)) / scale,
        status=enum_name(sdk().model.ProtoOAPositionStatus, p.positionStatus).replace("POSITION_STATUS_", ""),
        open_ts_ms=opt(td, "openTimestamp"), update_ts_ms=opt(p, "utcLastUpdateTimestamp"),
        label=opt(td, "label"), comment=opt(td, "comment"))


def parse_deal(d: Any) -> BrokerDeal:
    digits = int(opt(d, "moneyDigits", 2))
    scale = 10 ** digits
    is_close = has(d, "closePositionDetail")
    cd = d.closePositionDetail if is_close else None
    if cd is not None and has(cd, "moneyDigits"):
        cscale = 10 ** int(cd.moneyDigits)
    else:
        cscale = scale
    return BrokerDeal(
        deal_id=int(d.dealId), order_id=int(opt(d, "orderId", 0)) or None, position_id=int(d.positionId),
        symbol_id=int(d.symbolId), side=side_name(d.tradeSide), volume=int(d.volume),
        filled_volume=int(opt(d, "filledVolume", d.volume)), price=opt(d, "executionPrice"),
        status=enum_name(sdk().model.ProtoOADealStatus, d.dealStatus), commission=int(opt(d, "commission", 0)) / scale,
        exec_ts_ms=opt(d, "executionTimestamp"), is_close=is_close,
        gross_profit=(int(cd.grossProfit) / cscale) if cd is not None else 0.0,
        swap=(int(cd.swap) / cscale) if cd is not None else 0.0,
        close_commission=(int(cd.commission) / cscale) if cd is not None else 0.0,
        entry_price=opt(cd, "entryPrice") if cd is not None else None,
        closed_volume=int(opt(cd, "closedVolume", 0)) if cd is not None else 0,
        balance=(int(cd.balance) / cscale) if cd is not None and has(cd, "balance") else None)


def build_reconcile_req(ctid: int):
    r = sdk().msgs.ProtoOAReconcileReq()
    r.ctidTraderAccountId = ctid
    return r


def build_close_req(ctid: int, position_id: int, volume: int):
    r = sdk().msgs.ProtoOAClosePositionReq()
    r.ctidTraderAccountId = ctid
    r.positionId = position_id
    r.volume = volume
    return r


def build_amend_sltp_req(ctid: int, position_id: int, stop_loss: float | None, take_profit: float | None):
    r = sdk().msgs.ProtoOAAmendPositionSLTPReq()
    r.ctidTraderAccountId = ctid
    r.positionId = position_id
    if stop_loss is not None:
        r.stopLoss = stop_loss
    if take_profit is not None:
        r.takeProfit = take_profit
    return r


def build_deals_req(ctid: int, from_ms: int, to_ms: int, max_rows: int = 500):
    r = sdk().msgs.ProtoOADealListReq()
    r.ctidTraderAccountId = ctid
    r.fromTimestamp = from_ms
    r.toTimestamp = to_ms
    r.maxRows = max_rows
    return r
