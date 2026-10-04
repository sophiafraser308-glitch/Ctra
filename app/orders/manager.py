"""Order Manager — the only component allowed to send orders to the broker.

Safety rules implemented here:
  * DB-backed idempotency: `request_id` is UNIQUE; a repeated submit returns the existing order and NEVER re-sends.
  * The PROCESSING state is committed BEFORE the request leaves the process, so a broker order can never exist
    without a local record.
  * Timeout / unknown outcome  -> status UNKNOWN. Never blindly resent; resolved through reconciliation.
  * Writes are never auto-retried. A retry is a NEW request that passes the Risk Engine again, and is only
    offered after the broker state is known (retry_allowed flag set by reconciliation).
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit import AuditService
from app.config import Settings
from app.core.enums import ACTIVE_ORDER_STATUSES, Category, OrderStatus, Severity, TERMINAL_ORDER_STATUSES
from app.core.exceptions import (CTraderError, CTraderNotConnected, CTraderTimeout, NotFoundError, SafetyError,
                                 ValidationFailed)
from app.core.utils import new_correlation_id, new_id, utcnow
from app.ctrader.gateway import CTraderGateway
from app.ctrader.types import ExecutionEventData
from app.database import Database
from app.logging import get_logger
from app.models import Bot, Order, TradingAccount
from app.notifications.service import Notifier
from app.schemas.trading import OrderIntent, SignalIn
from app.services.settings_service import SystemSettingsService
from app.workers.heartbeat import HeartbeatService

log = get_logger(Category.ORDER)

_RANK = {"PENDING": 0, "PROCESSING": 1, "UNKNOWN": 1, "FAILED": 2, "SUBMITTED": 2, "ACCEPTED": 3,
         "PARTIALLY_FILLED": 4, "FILLED": 5, "REJECTED": 5, "CANCELLED": 5}
_EXEC_TO_STATUS = {"ORDER_ACCEPTED": OrderStatus.ACCEPTED, "ORDER_FILLED": OrderStatus.FILLED,
                   "ORDER_PARTIAL_FILL": OrderStatus.PARTIALLY_FILLED, "ORDER_REJECTED": OrderStatus.REJECTED,
                   "ORDER_CANCELLED": OrderStatus.CANCELLED, "ORDER_EXPIRED": OrderStatus.CANCELLED}


def client_order_id_for(request_id: str) -> str:
    """Deterministic, <=50 chars, broker-visible identifier used for later matching."""
    return "TP" + hashlib.sha256(request_id.encode()).hexdigest()[:30]


class OrderManager:
    def __init__(self, db: Database, settings: Settings, sys_settings: SystemSettingsService, gateway: CTraderGateway,
                 audit: AuditService, notifier: Notifier, hb: HeartbeatService, positions: Any) -> None:
        self.db, self.s, self.sys, self.gateway = db, settings, sys_settings, gateway
        self.audit, self.notifier, self.hb, self.positions = audit, notifier, hb, positions
        self.recon: Any = None     # late-bound
        self.risk: Any = None      # late-bound
        self._bg: set[asyncio.Task] = set()
        gateway.execution_handlers.append(self.handle_execution)

    def _spawn(self, coro) -> None:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # ---- persistence helper (retries so a transient DB error cannot lose the outcome) -----------
    async def _update(self, order_id: str, **fields: Any) -> Order:
        last: Exception | None = None
        for attempt in range(4):
            try:
                async with self.db.session() as s:
                    o = await s.get(Order, order_id)
                    for k, v in fields.items():
                        setattr(o, k, v)
                    await s.flush()
                    return o
            except Exception as exc:
                last = exc
                await asyncio.sleep(0.3 * (attempt + 1))
        log.critical("ORDER STATE UPDATE FAILED order=%s fields=%s err=%s — reconciliation must repair", order_id, list(fields), type(last).__name__)
        raise last  # type: ignore[misc]

    async def get(self, order_id: str) -> Order:
        async with self.db.session() as s:
            o = await s.get(Order, order_id)
        if o is None:
            raise NotFoundError("Order not found")
        return o

    async def list(self, *, statuses: list[str] | None = None, account_id: str | None = None, offset: int = 0, limit: int = 50) -> list[Order]:
        async with self.db.session() as s:
            q = select(Order).order_by(Order.created_at.desc()).offset(offset).limit(limit)
            if statuses:
                q = q.where(Order.status.in_(statuses))
            if account_id:
                q = q.where(Order.account_id == account_id)
            return list((await s.execute(q)).scalars())

    async def count(self, statuses: list[str] | None = None) -> int:
        from sqlalchemy import func
        async with self.db.session() as s:
            q = select(func.count()).select_from(Order)
            if statuses:
                q = q.where(Order.status.in_(statuses))
            return int((await s.execute(q)).scalar_one())

    # ---- submit ------------------------------------------------------------------------------------
    async def submit(self, intent: OrderIntent) -> Order:
        # 1. idempotent creation
        async with self.db.session() as s:
            existing = (await s.execute(select(Order).where(Order.request_id == intent.request_id))).scalar_one_or_none()
        if existing:
            log.info("duplicate submit ignored request_id=%s status=%s", intent.request_id, existing.status)
            return existing
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, intent.account_id)
        if acc is None or acc.deleted_at is not None:
            raise NotFoundError("Account not found")
        order = Order(request_id=intent.request_id, client_order_id=client_order_id_for(intent.request_id),
                      correlation_id=intent.correlation_id, account_id=intent.account_id, bot_id=intent.bot_id,
                      strategy_id=intent.strategy_id, strategy_version_id=intent.strategy_version_id, signal_id=intent.signal_id,
                      symbol=intent.symbol, side=intent.side, order_type=intent.order_type, volume_lots=intent.volume_lots,
                      price=intent.price, stop_loss_pips=intent.stop_loss_pips, take_profit_pips=intent.take_profit_pips,
                      status=OrderStatus.PENDING.value, retry_of=intent.retry_of, extra={"comment": intent.comment})
        try:
            async with self.db.session() as s:
                s.add(order)
        except IntegrityError:
            async with self.db.session() as s:
                return (await s.execute(select(Order).where(Order.request_id == intent.request_id))).scalar_one()
        oid = order.id
        await self.audit.record(action="ORDER_CREATED", target=oid, correlation_id=intent.correlation_id,
                                new_state={"symbol": intent.symbol, "side": intent.side, "lots": intent.volume_lots, "bot": intent.bot_id, "env": acc.environment})
        # 2. hard gates (defence in depth — Risk Engine already checked)
        if self.sys.new_trading_disabled:
            return await self._reject_local(oid, "NEW_TRADING_DISABLED")
        if acc.environment == "LIVE" and not self.sys.live_allowed:
            return await self._reject_local(oid, "LIVE_TRADING_DISABLED")
        if not acc.trading_enabled:
            return await self._reject_local(oid, "ACCOUNT_TRADING_DISABLED")
        if not self.gateway.is_connected(acc.id):
            return await self._fail_unsent(oid, "ACCOUNT_NOT_CONNECTED")
        # 3. symbol / volume validation
        try:
            sym = await self.gateway.get_symbol(acc.id, intent.symbol)
        except Exception as exc:
            return await self._reject_local(oid, f"SYMBOL_UNAVAILABLE: {str(exc)[:80]}")
        vol = sym.lots_to_protocol(intent.volume_lots)
        if vol < sym.min_volume or vol > sym.max_volume:
            return await self._reject_local(oid, f"VOLUME_OUT_OF_RANGE min={sym.min_lots:g} max={sym.max_lots:g}")
        if intent.order_type in ("LIMIT", "STOP") and not intent.price:
            return await self._reject_local(oid, "PRICE_REQUIRED")
        pip = sym.pip_size
        sl_abs = tp_abs = rel_sl = rel_tp = None
        if intent.order_type == "MARKET":
            rel_sl = intent.stop_loss_pips * pip if intent.stop_loss_pips else None
            rel_tp = intent.take_profit_pips * pip if intent.take_profit_pips else None
        else:
            sign = 1 if intent.side == "BUY" else -1
            sl_abs = intent.price - sign * intent.stop_loss_pips * pip if intent.stop_loss_pips else None
            tp_abs = intent.price + sign * intent.take_profit_pips * pip if intent.take_profit_pips else None
        # 4. commit PROCESSING *before* sending
        await self._update(oid, status=OrderStatus.PROCESSING.value, symbol_id=sym.symbol_id, protocol_volume=vol,
                           stop_loss=sl_abs, take_profit=tp_abs)
        # 5. send (single attempt)
        try:
            await self._update(oid, submitted_at=utcnow())
            evt = await self.gateway.new_order(acc.id, sym, side=intent.side, order_type=intent.order_type, protocol_volume=vol,
                                               client_order_id=order.client_order_id, price=intent.price, stop_loss=sl_abs, take_profit=tp_abs,
                                               rel_stop_loss_price=rel_sl, rel_take_profit_price=rel_tp,
                                               comment=(intent.comment or f"bot:{intent.bot_id}")[:100])
        except CTraderNotConnected:
            return await self._fail_unsent(oid, "NOT_CONNECTED_BEFORE_SEND")      # provably not sent
        except CTraderTimeout:
            return await self._mark_unknown(oid, "TIMEOUT waiting for broker response")
        except CTraderError as exc:
            if exc.broker_code:        # explicit broker error response => not executed
                return await self._reject_local(oid, f"BROKER:{exc.broker_code}:{str(exc)[:80]}")
            return await self._mark_unknown(oid, f"{type(exc).__name__}: {str(exc)[:120]}")
        except Exception as exc:
            log.error("order send crashed: %s", type(exc).__name__, exc_info=True)
            return await self._mark_unknown(oid, f"unexpected {type(exc).__name__}")
        await self._update(oid, status=OrderStatus.SUBMITTED.value)
        self.hb.update("order_manager", "OK", operation=f"submitted {oid}", order=True)
        await self.handle_execution(acc.id, evt)       # idempotent: the stream may deliver the same event
        return await self.get(oid)

    async def _reject_local(self, oid: str, reason: str) -> Order:
        o = await self._update(oid, status=OrderStatus.REJECTED.value, reject_reason=reason[:128], resolved_at=utcnow())
        log.warning("order rejected %s: %s", oid, reason, ctx={"order": oid})
        await self.audit.record(action="ORDER_REJECTED", target=oid, result="REJECTED", error=reason)
        await self.notifier.notify("ORDER_REJECTED", f"Order {oid} {o.symbol} {o.side} rejected: {reason}", Severity.WARNING, dedup_key=f"rej:{reason[:30]}", throttle_seconds=120)
        return o

    async def _fail_unsent(self, oid: str, reason: str) -> Order:
        o = await self._update(oid, status=OrderStatus.FAILED.value, reject_reason=reason, resolved_at=utcnow(),
                               extra={"retry_allowed": True, "unsent": True})
        await self.audit.record(action="ORDER_FAILED", target=oid, result="FAILED", error=reason)
        await self.notifier.notify("ORDER_FAILED", f"Order {oid} {o.symbol} not sent: {reason}", Severity.WARNING, dedup_key=f"fail:{reason}", throttle_seconds=120)
        return o

    async def _mark_unknown(self, oid: str, why: str) -> Order:
        o = await self._update(oid, status=OrderStatus.UNKNOWN.value, error=why[:300])
        log.error("ORDER UNKNOWN %s: %s — will reconcile (no blind resend)", oid, why)
        await self.audit.record(action="ORDER_UNKNOWN", target=oid, result="UNKNOWN", error=why)
        await self.notifier.notify("ORDER_UNKNOWN", f"❓ Order {oid} {o.symbol} {o.side} outcome UNKNOWN ({why}). Reconciling with broker; it will NOT be resent automatically.", Severity.ERROR, dedup_key=f"unk:{oid}", throttle_seconds=0)
        if self.recon:
            self._spawn(self.recon.resolve_unknown(oid))
        return o

    # ---- broker events -----------------------------------------------------------------------------------
    async def handle_execution(self, account_id: str, evt: ExecutionEventData) -> None:
        self.hb.update("ctrader", "OK", ctrader_event=True)
        order: Order | None = None
        bo = evt.order
        async with self.db.session() as s:
            if bo and bo.client_order_id:
                order = (await s.execute(select(Order).where(Order.client_order_id == bo.client_order_id))).scalar_one_or_none()
            if order is None and bo:
                order = (await s.execute(select(Order).where(Order.account_id == account_id, Order.broker_order_id == bo.order_id))).scalar_one_or_none()
        new_status = _EXEC_TO_STATUS.get(evt.execution_type)
        if order is not None and new_status is not None:
            fields: dict[str, Any] = {}
            if _RANK[new_status.value] >= _RANK.get(order.status, 0):
                fields["status"] = new_status.value
            if bo:
                fields["broker_order_id"] = bo.order_id
                if bo.position_id:
                    fields["broker_position_id"] = bo.position_id
                if bo.executed_volume and order.protocol_volume:
                    fields["filled_volume_lots"] = order.volume_lots * bo.executed_volume / order.protocol_volume
                if bo.execution_price:
                    fields["avg_fill_price"] = bo.execution_price
            if evt.position and evt.position.position_id:
                fields["broker_position_id"] = evt.position.position_id
            if new_status in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED):
                fields["resolved_at"] = utcnow()
                if new_status == OrderStatus.FILLED:
                    fields["filled_volume_lots"] = order.volume_lots
            if new_status == OrderStatus.REJECTED:
                fields["reject_reason"] = (evt.error_code or "BROKER_REJECTED")[:128]
            prev = order.status
            order = await self._update(order.id, **fields)
            if order.status != prev:
                await self.audit.record(action=f"ORDER_{order.status}", target=order.id, correlation_id=order.correlation_id,
                                        previous_state={"status": prev}, new_state={"status": order.status, "broker_order": order.broker_order_id, "position": order.broker_position_id, "price": order.avg_fill_price})
                log.notice("order %s %s -> %s", order.id, prev, order.status, ctx={"order": order.id, "corr": order.correlation_id})
                if order.status == "FILLED":
                    await self.notifier.notify("ORDER_FILLED", f"✅ {order.symbol} {order.side} {order.volume_lots:g} lots filled @ {order.avg_fill_price}", Severity.INFO, dedup_key=f"fill:{order.id}", throttle_seconds=0)
                    if order.bot_id:
                        async with self.db.session() as s:
                            b = await s.get(Bot, order.bot_id)
                            if b:
                                b.last_order_at = utcnow()
                elif order.status == "REJECTED":
                    await self.notifier.notify("ORDER_REJECTED", f"Order {order.id} {order.symbol} rejected by broker: {order.reject_reason}", Severity.WARNING, dedup_key=f"rej:{order.id}", throttle_seconds=0)
        await self.positions.apply_execution(account_id, evt, order)
        if self.recon and evt.execution_type in ("ORDER_FILLED", "ORDER_PARTIAL_FILL") and order is None and evt.position is not None:
            pass  # external fill: positions service recorded it as EXTERNAL

    # ---- user / system actions ------------------------------------------------------------------------------
    async def cancel(self, order_id: str, user_id: int | None) -> Order:
        o = await self.get(order_id)
        if o.status not in (OrderStatus.ACCEPTED.value, OrderStatus.SUBMITTED.value) or not o.broker_order_id:
            raise ValidationFailed(f"Order in state {o.status} cannot be cancelled")
        try:
            await self.gateway.cancel_order(o.account_id, o.broker_order_id)
        except CTraderError as exc:
            await self.audit.record(action="ORDER_CANCEL", user_id=user_id, target=o.id, result="FAILED", error=str(exc)[:200])
            raise
        o = await self._update(o.id, status=OrderStatus.CANCELLED.value, resolved_at=utcnow())
        await self.audit.record(action="ORDER_CANCEL", user_id=user_id, target=o.id)
        return o

    async def cancel_all_pending(self, account_id: str | None, user_id: int | None) -> dict[str, int]:
        ok = failed = 0
        for o in await self.list(statuses=[OrderStatus.ACCEPTED.value, OrderStatus.SUBMITTED.value], account_id=account_id, limit=500):
            if not o.broker_order_id or o.order_type == "MARKET":
                continue
            try:
                await self.cancel(o.id, user_id)
                ok += 1
            except Exception:
                failed += 1
        return {"cancelled": ok, "failed": failed}

    async def retry(self, order_id: str, user_id: int) -> Order:
        """Explicit, audited retry: only after the broker state is known; re-runs the Risk Engine."""
        o = await self.get(order_id)
        if o.status == OrderStatus.UNKNOWN.value:
            raise SafetyError("Order outcome still UNKNOWN — reconcile first; blind retry could duplicate the trade")
        if not (o.extra or {}).get("retry_allowed"):
            raise ValidationFailed("Retry is not allowed for this order")
        if self.risk is None:
            raise SafetyError("Risk engine unavailable")
        async with self.db.session() as s:
            acc = await s.get(TradingAccount, o.account_id)
        sig = SignalIn(symbol=o.symbol, side=o.side, order_type=o.order_type, price=o.price,
                       stop_loss_pips=o.stop_loss_pips, take_profit_pips=o.take_profit_pips)
        sym = await self.gateway.get_symbol(o.account_id, o.symbol)
        dec = await self.risk.evaluate(account=acc, signal=sig, sym=sym, bot_id=o.bot_id, strategy_id=o.strategy_id)
        if not dec.approved:
            raise SafetyError(f"Risk Engine rejected the retry: {dec.reason}")
        new = await self.submit(OrderIntent(request_id=f"{o.request_id}-r{new_id()[:4]}", correlation_id=new_correlation_id(),
                                            account_id=o.account_id, symbol=o.symbol, side=o.side, order_type=o.order_type,
                                            volume_lots=dec.lots, price=o.price, stop_loss_pips=o.stop_loss_pips,
                                            take_profit_pips=o.take_profit_pips, bot_id=o.bot_id, strategy_id=o.strategy_id,
                                            strategy_version_id=o.strategy_version_id, signal_id=o.signal_id, retry_of=o.id))
        await self._update(o.id, extra={**(o.extra or {}), "retry_allowed": False, "retried_as": new.id})
        await self.audit.record(action="ORDER_RETRY", user_id=user_id, target=o.id, new_state={"new_order": new.id})
        return new

    async def stuck_processing(self, older_than_s: int = 60) -> list[Order]:
        from datetime import timedelta
        cutoff = utcnow() - timedelta(seconds=older_than_s)
        async with self.db.session() as s:
            return list((await s.execute(select(Order).where(Order.status.in_([OrderStatus.PROCESSING.value, OrderStatus.PENDING.value]), Order.updated_at < cutoff))).scalars())
