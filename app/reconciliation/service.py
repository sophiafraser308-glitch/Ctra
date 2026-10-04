"""Reconciliation: the broker is authoritative. Detects and repairs local/broker divergence.

Kinds: ORPHAN_POSITION, PHANTOM_POSITION, MISSING_POSITION, POSITION_MISMATCH, UNEXPECTED_ORDER, MISSING_ORDER.
Triggers: startup, reconnect, unknown_order, periodic, critical_error, manual.
"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.audit import AuditService
from app.config import Settings
from app.core.enums import ACTIVE_ORDER_STATUSES, Category, OrderStatus, Severity
from app.core.exceptions import CTraderError
from app.core.utils import new_id, utcnow
from app.ctrader.gateway import CTraderGateway
from app.ctrader.types import BrokerOrder, BrokerPosition
from app.database import Database
from app.logging import get_logger
from app.models import Order, Position, ReconciliationEvent, TradingAccount
from app.notifications.service import Notifier
from app.workers.heartbeat import HeartbeatService

log = get_logger(Category.RECONCILIATION)
_MAP = {"ACCEPTED": OrderStatus.ACCEPTED, "FILLED": OrderStatus.FILLED, "REJECTED": OrderStatus.REJECTED,
        "EXPIRED": OrderStatus.CANCELLED, "CANCELLED": OrderStatus.CANCELLED}


class ReconciliationService:
    def __init__(self, db: Database, settings: Settings, gateway: CTraderGateway, positions: Any, orders: Any,
                 audit: AuditService, notifier: Notifier, hb: HeartbeatService) -> None:
        self.db, self.s, self.gateway, self.positions, self.orders = db, settings, gateway, positions, orders
        self.audit, self.notifier, self.hb = audit, notifier, hb
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_run: dict[str, float] = {}
        self.last_run_at = None
        self._task: asyncio.Task | None = None
        self.accounts: Any = None   # late-bound AccountService

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="reconciliation")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.reconcile_interval_seconds)
            try:
                await self.reconcile_all("periodic")
            except Exception as exc:
                log.error("periodic reconciliation failed: %s", type(exc).__name__, exc_info=True)

    async def reconcile_all(self, trigger: str) -> dict[str, int]:
        total: dict[str, int] = {}
        async with self.db.session() as s:
            accs = (await s.execute(select(TradingAccount).where(TradingAccount.deleted_at.is_(None)))).scalars().all()
        for a in accs:
            if self.gateway.is_connected(a.id):
                try:
                    for k, v in (await self.reconcile(a.id, trigger)).items():
                        total[k] = total.get(k, 0) + v
                except Exception as exc:
                    log.error("reconcile %s failed: %s", a.id, str(exc)[:150])
        return total

    async def _event(self, account_id: str, trigger: str, kind: str, severity: str, local: Any, broker: Any, action: str) -> None:
        async with self.db.session() as s:
            s.add(ReconciliationEvent(account_id=account_id, trigger=trigger, kind=kind, severity=severity,
                                      local_state=local, broker_state=broker, action=action))
        log.log(30 if severity != "INFO" else 20, "RECON %s %s: %s", account_id, kind, action)

    @staticmethod
    def _bp_dict(p: BrokerPosition) -> dict:
        return {"id": p.position_id, "symbol_id": p.symbol_id, "side": p.side, "volume": p.volume, "sl": p.stop_loss, "tp": p.take_profit, "entry": p.entry_price}

    async def reconcile(self, account_id: str, trigger: str) -> dict[str, int]:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        counts: dict[str, int] = {}

        def bump(k: str) -> None:
            counts[k] = counts.get(k, 0) + 1

        async with lock:
            bpos, bord = await self.gateway.reconcile_snapshot(account_id)
            sess = self.gateway.session(account_id)
            await self.gateway.ensure_details(account_id, list({p.symbol_id for p in bpos} | {o.symbol_id for o in bord}))
            bpos_by_id = {p.position_id: p for p in bpos}
            bord_by_id = {o.order_id: o for o in bord}
            async with self.db.session() as s:
                lpos = list((await s.execute(select(Position).where(Position.account_id == account_id, Position.status == "OPEN"))).scalars())
                lord = list((await s.execute(select(Order).where(Order.account_id == account_id,
                                                                 Order.status.in_([x.value for x in ACTIVE_ORDER_STATUSES] + ["UNKNOWN"])))).scalars())
                recent_filled = list((await s.execute(select(Order).where(Order.account_id == account_id, Order.status == "FILLED",
                                                                          Order.broker_position_id.is_not(None),
                                                                          Order.created_at > utcnow() - timedelta(days=2)))).scalars())
            lpos_by_id = {p.broker_position_id: p for p in lpos}
            filled_by_pos = {o.broker_position_id: o for o in recent_filled}

            # broker positions unknown locally
            for bp in bpos:
                if bp.position_id in lpos_by_id:
                    continue
                order = filled_by_pos.get(bp.position_id)
                pos, _ = await self.positions.upsert_from_broker(account_id, bp, order=order, source="BOT" if order else "EXTERNAL")
                kind = "MISSING_POSITION" if order else "ORPHAN_POSITION"
                bump(kind)
                action = "local record rebuilt from filled order" if order else "adopted as EXTERNAL position (not owned by any bot)"
                await self._event(account_id, trigger, kind, "WARNING", None, self._bp_dict(bp), action)
                await self.notifier.notify("RECONCILIATION", f"{kind} on account {account_id}: {pos.symbol} {pos.side} {pos.volume_lots:g} — {action}", Severity.WARNING, dedup_key=f"recon:{kind}:{bp.position_id}")
            # local positions missing at broker
            for lp in lpos:
                if lp.broker_position_id in bpos_by_id:
                    continue
                bump("PHANTOM_POSITION")
                action = await self._resolve_phantom(account_id, lp)
                await self._event(account_id, trigger, "PHANTOM_POSITION", "WARNING", {"id": lp.id, "broker_position": lp.broker_position_id, "symbol": lp.symbol, "lots": lp.volume_lots}, None, action)
                await self.notifier.notify("RECONCILIATION", f"PHANTOM position {lp.symbol} {lp.side} ({lp.id}) not at broker — {action}", Severity.WARNING, dedup_key=f"recon:phantom:{lp.id}")
            # mismatches
            for lp in lpos:
                bp = bpos_by_id.get(lp.broker_position_id)
                if not bp:
                    continue
                sym = sess.symbols_by_id.get(bp.symbol_id)
                tol = (10 ** -(sym.digits if sym else 5)) / 2
                blots = sym.protocol_to_lots(bp.volume) if sym else lp.volume_lots
                diffs = {}
                if abs(blots - lp.volume_lots) > 1e-9:
                    diffs["volume"] = (lp.volume_lots, blots)
                if (lp.stop_loss or 0) and (bp.stop_loss or 0) and abs((lp.stop_loss or 0) - (bp.stop_loss or 0)) > tol or bool(lp.stop_loss) != bool(bp.stop_loss):
                    diffs["sl"] = (lp.stop_loss, bp.stop_loss)
                if (lp.take_profit or 0) and (bp.take_profit or 0) and abs((lp.take_profit or 0) - (bp.take_profit or 0)) > tol or bool(lp.take_profit) != bool(bp.take_profit):
                    diffs["tp"] = (lp.take_profit, bp.take_profit)
                if diffs:
                    bump("POSITION_MISMATCH")
                    await self.positions.upsert_from_broker(account_id, bp)
                    await self._event(account_id, trigger, "POSITION_MISMATCH", "WARNING", {"id": lp.id, "diffs": {k: v[0] for k, v in diffs.items()}}, {k: v[1] for k, v in diffs.items()}, "local state overwritten with broker values")
            # broker pending orders unknown locally
            local_broker_ids = {o.broker_order_id for o in lord if o.broker_order_id}
            local_client_ids = {o.client_order_id for o in lord}
            for bo in bord:
                if bo.order_id in local_broker_ids or (bo.client_order_id and bo.client_order_id in local_client_ids):
                    # link broker id if we only knew the client id
                    for o in lord:
                        if o.client_order_id == bo.client_order_id and not o.broker_order_id:
                            await self.orders._update(o.id, broker_order_id=bo.order_id, status=OrderStatus.ACCEPTED.value if o.status in ("UNKNOWN", "PROCESSING", "SUBMITTED") else o.status)
                    continue
                sym = sess.symbols_by_id.get(bo.symbol_id)
                created_ext = False
                async with self.db.session() as s:
                    exists = (await s.execute(select(Order).where(Order.account_id == account_id, Order.broker_order_id == bo.order_id))).scalar_one_or_none()
                    if exists is None:
                        s.add(Order(request_id=f"ext-{account_id}-{bo.order_id}", client_order_id=(bo.client_order_id or f"EXT{bo.order_id}")[:50], correlation_id=new_id(),
                                    account_id=account_id, symbol=sym.name if sym else str(bo.symbol_id), symbol_id=bo.symbol_id, side=bo.side,
                                    order_type=bo.order_type if bo.order_type in ("MARKET", "LIMIT", "STOP") else "LIMIT",
                                    volume_lots=sym.protocol_to_lots(bo.volume) if sym else 0.0, protocol_volume=bo.volume,
                                    price=bo.limit_price or bo.stop_price, stop_loss=bo.stop_loss, take_profit=bo.take_profit,
                                    status=OrderStatus.ACCEPTED.value, broker_order_id=bo.order_id, extra={"external": True}))
                        bump("UNEXPECTED_ORDER")
                        created_ext = True
                if created_ext:
                    await self._event(account_id, trigger, "UNEXPECTED_ORDER", "WARNING", None, {"order": bo.order_id, "type": bo.order_type}, "recorded as external order")
            # local orders the broker does not list
            for o in lord:
                if o.status in ("UNKNOWN", "PROCESSING", "PENDING") and not o.broker_order_id:
                    if o.status != "PENDING" or (utcnow() - o.created_at).total_seconds() > 60:
                        res = await self.resolve_unknown(o.id)
                        bump("MISSING_ORDER")
                        await self._event(account_id, trigger, "MISSING_ORDER", "WARNING", {"order": o.id, "status": o.status}, None, f"resolve_unknown -> {res}")
                elif o.broker_order_id and o.broker_order_id not in bord_by_id and o.status in ("ACCEPTED", "SUBMITTED", "PARTIALLY_FILLED") and o.order_type != "MARKET":
                    bump("MISSING_ORDER")
                    res = await self.resolve_unknown(o.id)
                    await self._event(account_id, trigger, "MISSING_ORDER", "WARNING", {"order": o.id, "status": o.status}, None, f"resolve_unknown -> {res}")
            if not counts and trigger != "periodic":
                await self._event(account_id, trigger, "CLEAN", "INFO", None, None, "no divergence")
        self.last_run[account_id] = time.monotonic()
        self.last_run_at = utcnow()
        self.hb.update("reconciliation", "OK", operation=f"{trigger} {account_id}", divergences=sum(counts.values()))
        return counts

    async def _resolve_phantom(self, account_id: str, lp: Position) -> str:
        """Local says open, broker says gone: look for the closing deal to record the real result."""
        try:
            now_ms = int(time.time() * 1000)
            deals = await self.gateway.deal_history(account_id, now_ms - 7 * 86_400_000, now_ms)
            closing = [d for d in deals if d.position_id == lp.broker_position_id and d.is_close]
            for d in closing:
                await self.positions.record_close_deal(account_id, d, fully_closed=True)
            if closing:
                async with self.db.session() as s:
                    p = await s.get(Position, lp.id)
                    if p and p.status == "OPEN":
                        p.status, p.closed_at, p.unrealized_pnl = "CLOSED", utcnow(), 0.0
                return f"closed at broker; {len(closing)} closing deal(s) recorded"
        except CTraderError as exc:
            log.warning("deal history lookup failed: %s", str(exc)[:100])
        async with self.db.session() as s:
            p = await s.get(Position, lp.id)
            if p:
                p.status, p.closed_at, p.unrealized_pnl = "CLOSED", utcnow(), 0.0
        return "marked CLOSED (closing deal not found; P/L unknown)"

    # ---- UNKNOWN order resolution -----------------------------------------------------------------------
    async def resolve_unknown(self, order_id: str) -> str:
        """Find out what really happened to an order whose outcome is unknown. NEVER resends."""
        o = await self.orders.get(order_id)
        if o.status not in ("UNKNOWN", "PROCESSING", "PENDING", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED"):
            return f"already {o.status}"
        if not self.gateway.is_connected(o.account_id):
            return "account not connected; will retry on reconnect"
        now_ms = int(time.time() * 1000)
        frm = int((o.created_at.timestamp() - 600) * 1000)
        try:
            history = await self.gateway.order_history(o.account_id, frm, now_ms)
            bpos, bord = await self.gateway.reconcile_snapshot(o.account_id)
        except CTraderError as exc:
            return f"broker query failed: {str(exc)[:80]}"
        found: BrokerOrder | None = next((b for b in history + bord if b.client_order_id == o.client_order_id or (o.broker_order_id and b.order_id == o.broker_order_id)), None)
        if found is None:
            await self.orders._update(o.id, status=OrderStatus.FAILED.value, reject_reason="NOT_FOUND_ON_BROKER", resolved_at=utcnow(),
                                      extra={**(o.extra or {}), "retry_allowed": True, "resolved_by": "reconciliation"})
            await self.audit.record(action="ORDER_UNKNOWN_RESOLVED", target=o.id, new_state={"result": "NOT_FOUND_ON_BROKER", "retry_allowed": True})
            await self.notifier.notify("ORDER_RESOLVED", f"Order {o.id} {o.symbol}: broker has no record — treated as NOT executed. A manual retry is now allowed (Orders menu).", Severity.NOTICE, dedup_key=f"res:{o.id}", throttle_seconds=0)
            return "NOT_FOUND_ON_BROKER (retry allowed)"
        status = _MAP.get(found.status, OrderStatus.ACCEPTED)
        fields: dict[str, Any] = {"status": status.value, "broker_order_id": found.order_id}
        if found.position_id:
            fields["broker_position_id"] = found.position_id
        if found.execution_price:
            fields["avg_fill_price"] = found.execution_price
        if status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            fields["resolved_at"] = utcnow()
        if status == OrderStatus.FILLED:
            fields["filled_volume_lots"] = o.volume_lots
        fresh = await self.orders._update(o.id, **fields)
        if status == OrderStatus.FILLED and found.position_id:
            bp = next((p for p in bpos if p.position_id == found.position_id), None)
            if bp:
                await self.positions.upsert_from_broker(o.account_id, bp, order=fresh)
        await self.audit.record(action="ORDER_UNKNOWN_RESOLVED", target=o.id, new_state={"result": status.value, "broker_order": found.order_id})
        await self.notifier.notify("ORDER_RESOLVED", f"Order {o.id} {o.symbol} resolved from broker: {status.value}", Severity.NOTICE, dedup_key=f"res:{o.id}", throttle_seconds=0)
        return f"FOUND {status.value}"
