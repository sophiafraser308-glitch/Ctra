"""Protocol helpers for orders & execution events."""
from __future__ import annotations

from typing import Any

from app.ctrader.pb import enum_name, has, opt, sdk
from app.ctrader.positions import parse_deal, parse_position, side_name
from app.ctrader.types import PRICE_SCALE, BrokerOrder, ExecutionEventData, SymbolInfo


def parse_order(o: Any) -> BrokerOrder:
    td = o.tradeData
    m = sdk().model
    return BrokerOrder(
        order_id=int(o.orderId), symbol_id=int(td.symbolId), side=side_name(td.tradeSide),
        order_type=enum_name(m.ProtoOAOrderType, o.orderType),
        status=enum_name(m.ProtoOAOrderStatus, o.orderStatus).replace("ORDER_STATUS_", ""),
        volume=int(td.volume), executed_volume=int(opt(o, "executedVolume", 0)),
        limit_price=opt(o, "limitPrice"), stop_price=opt(o, "stopPrice"),
        stop_loss=opt(o, "stopLoss"), take_profit=opt(o, "takeProfit"),
        execution_price=opt(o, "executionPrice"), client_order_id=opt(o, "clientOrderId"),
        position_id=int(opt(o, "positionId", 0)) or None, update_ts_ms=opt(o, "utcLastUpdateTimestamp"),
        comment=opt(td, "comment"))


def parse_execution_event(ev: Any) -> ExecutionEventData:
    m = sdk().model
    return ExecutionEventData(
        ctid=int(ev.ctidTraderAccountId),
        execution_type=enum_name(m.ProtoOAExecutionType, ev.executionType),
        order=parse_order(ev.order) if has(ev, "order") else None,
        position=parse_position(ev.position) if has(ev, "position") else None,
        deal=parse_deal(ev.deal) if has(ev, "deal") else None,
        error_code=opt(ev, "errorCode"), is_server_event=bool(opt(ev, "isServerEvent", False)))


def build_new_order(ctid: int, sym: SymbolInfo, *, side: str, order_type: str, protocol_volume: int,
                    client_order_id: str, price: float | None = None, stop_loss: float | None = None,
                    take_profit: float | None = None, rel_stop_loss_price: float | None = None,
                    rel_take_profit_price: float | None = None, comment: str | None = None):
    """Market orders use *relative* SL/TP (absolute SL/TP are not accepted on market orders by cTrader)."""
    m = sdk().model
    r = sdk().msgs.ProtoOANewOrderReq()
    r.ctidTraderAccountId = ctid
    r.symbolId = sym.symbol_id
    r.orderType = getattr(m.ProtoOAOrderType, order_type)
    r.tradeSide = getattr(m.ProtoOATradeSide, side)
    r.volume = protocol_volume
    r.clientOrderId = client_order_id[:50]
    if comment:
        r.comment = comment[:512]
    if order_type == "LIMIT" and price is not None:
        r.limitPrice = sym.round_price(price)
    elif order_type == "STOP" and price is not None:
        r.stopPrice = sym.round_price(price)
    if order_type == "MARKET":
        if rel_stop_loss_price:
            r.relativeStopLoss = int(round(rel_stop_loss_price * PRICE_SCALE))
        if rel_take_profit_price:
            r.relativeTakeProfit = int(round(rel_take_profit_price * PRICE_SCALE))
    else:
        if stop_loss is not None:
            r.stopLoss = sym.round_price(stop_loss)
        if take_profit is not None:
            r.takeProfit = sym.round_price(take_profit)
    return r


def build_cancel_order(ctid: int, order_id: int):
    r = sdk().msgs.ProtoOACancelOrderReq()
    r.ctidTraderAccountId = ctid
    r.orderId = order_id
    return r


def build_order_list_req(ctid: int, from_ms: int, to_ms: int):
    r = sdk().msgs.ProtoOAOrderListReq()
    r.ctidTraderAccountId = ctid
    r.fromTimestamp = from_ms
    r.toTimestamp = to_ms
    return r
