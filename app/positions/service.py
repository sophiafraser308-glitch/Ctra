"""Position tracking, SL/TP modification, closing, and trade (P/L) recording. Broker is authoritative."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit import AuditService
from app.core.enums import Category, Severity
from app.core.exceptions import CTraderError, NotFoundError, SafetyError, ValidationFailed
from app.core.utils import utcnow
from app.ctrader.gateway import CTraderGateway
from app.ctrader.types import BrokerDeal, BrokerPosition, ExecutionEventData, SymbolInfo
from app.database import Database
from app.logging import get_logger
from app.models import Order, Position, Trade, TradingAccount
from app.notifications.service import Notifier

log = get_logger(Category.POSITION)


def _ts(ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if ms else None


class PositionService:
    def __init__(self, db: Database, gateway: CTraderGateway, audit: AuditService, notifier: Notifier) -> None:
        self.db, self.gateway, self.audit, self.notifier = db, gateway, audit, notifier
        self.risk: Any = None            # late-bound
        self.market: Any = None          # late-bound
        self.on_position_event: list[Any] = []   # async callables (account_id, kind, Position)

    # ---- helpers ------------------------------------------------------------------------
    async def _sym(self, account_id: str, symbol_id: int) -> SymbolInfo | None:
        try:
            info = self.gateway.symbol_by_id(account_id, symbol_id)
            if info and not info.detailed and self.gateway.is_connected(account_id):
                await self.gateway.ensure_details(account_id, [symbol_id])
            return info
        except Exception:
            return None

    async def _emit(self, account_id: str, kind: str, pos: Position) -> None:
        for h in self.on_position_event:
            try:
                await h(account_id, kind, pos)
            except Exception as exc:
                log.error("position event hook failed: %s", type(exc).__name__)

    # ---- queries --------------------------------------------------------------------------
    async def get(self, position_id: str) -> Position:
        async with self.db.session() as s:
            p = await s.get(Position, position_id)
        if p is None:
            raise NotFoundError("Position not found")
        return p

    async def list_open(self, account_id: str | None = None, offset: int = 0, limit: int = 100) -> list[Position]:
        async with self.db.session() as s:
            q = select(Position).where(Position.status == "OPEN").order_by(Position.opened_at.desc()).offset(offset).limit(limit)
            if account_id:
                q = q.where(Position.account_id == account_id)
            return list((await s.execute(q)).scalars())

    async def count_open(self, account_id: str | None = None) -> int:
        from sqlalchemy import func
        async with self.db.session() as s:
            q = select(func.count()).select_from(Position).where(Position.status == "OPEN")
            if account_id:
                q = q.where(Position.account_id == account_id)
            return int((await s.execute(q)).scalar_one())

    # ---- state from broker -------------------------------------------------------------------
    async def upsert_from_broker(self, account_id: str, bp: BrokerPosition, *, order: Order | None = None,
                                 source: str | None = None) -> tuple[Position, bool]:
        sym = await self._sym(account_id, bp.symbol_id)
        name = sym.name if sym else str(bp.symbol_id)
        lots = sym.protocol_to_lots(bp.volume) if sym else bp.volume / 10_000_000
        created = False
        async with self.db.session() as s:
            pos = (await s.execute(select(Position).where(Position.account_id == account_id,
                                                          Position.broker_position_id == bp.position_id))).scalar_one_or_none()
            if pos is None:
                pos = Position(account_id=account_id, broker_position_id=bp.position_id, symbol=name, symbol_id=bp.symbol_id,
                               side=bp.side, volume_lots=lots, source=source or ("BOT" if order else "EXTERNAL"),
                               opened_at=_ts(bp.open_ts_ms) or utcnow())
                if order:
                    pos.bot_id, pos.strategy_id, pos.strategy_version_id = order.bot_id, order.strategy_id, order.strategy_version_id
                    pos.order_id, pos.signal_id = order.id, order.signal_id
                s.add(pos)
                created = True
            pos.volume_lots, pos.entry_price = lots, bp.entry_price
            pos.stop_loss, pos.take_profit = bp.stop_loss, bp.take_profit
            pos.swap, pos.commission, pos.used_margin = bp.swap, bp.commission, bp.used_margin
            if bp.status in ("CLOSED",):
                pos.status, pos.closed_at = "CLOSED", pos.closed_at or utcnow()
            else:
                pos.status, pos.closed_at = "OPEN", None
            await s.flush()
            return pos, created

    async def apply_pnl(self, account_id: str, broker_positions: list[BrokerPosition], pnl: dict[int, float]) -> None:
        """Called after each account sync: refresh unrealized P/L and live fields of known open positions."""
        by_id = {p.position_id: p for p in broker_positions}
        async with self.db.session() as s:
            rows = (await s.execute(select(Position).where(Position.account_id == account_id, Position.status == "OPEN"))).scalars().all()
            for pos in rows:
                bp = by_id.get(pos.broker_position_id)
                if bp is None:
                    continue
                pos.unrealized_pnl = pnl.get(pos.broker_position_id)
                pos.used_margin, pos.swap, pos.commission = bp.used_margin, bp.swap, bp.commission
                pos.stop_loss, pos.take_profit = bp.stop_loss, bp.take_profit
                q = self.market.quote(account_id, pos.symbol_id) if (self.market and pos.symbol_id) else None
                if q:
                    pos.current_price = q.bid if pos.side == "BUY" else q.ask

    async def apply_execution(self, account_id: str, evt: ExecutionEventData, order: Order | None) -> None:
        """Idempotent: positions upserted by broker id; trades unique by deal id."""
        if evt.position is not None:
            try:
                pos, created = await self.upsert_from_broker(account_id, evt.position, order=order)
                if created:
                    log.info("position opened %s %s %.2f lots", pos.symbol, pos.side, pos.volume_lots)
                    await self.audit.record(action="POSITION_OPENED", target=pos.id, new_state={"symbol": pos.symbol, "side": pos.side, "lots": pos.volume_lots, "order": pos.order_id, "bot": pos.bot_id})
                    await self._emit(account_id, "OPENED", pos)
            except Exception as exc:
                log.error("position upsert failed: %s", type(exc).__name__, exc_info=True)
        if evt.deal is not None and evt.deal.is_close:
            await self.record_close_deal(account_id, evt.deal, fully_closed=(evt.position is not None and evt.position.status == "CLOSED"))

    async def record_close_deal(self, account_id: str, deal: BrokerDeal, *, fully_closed: bool | None = None) -> Trade | None:
        sym = await self._sym(account_id, deal.symbol_id)
        lots = sym.protocol_to_lots(deal.closed_volume or deal.filled_volume) if sym else 0.0
        async with self.db.session() as s:
            pos = (await s.execute(select(Position).where(Position.account_id == account_id,
                                                          Position.broker_position_id == deal.position_id))).scalar_one_or_none()
            net = deal.gross_profit + deal.swap + deal.close_commission
            trade = Trade(account_id=account_id, position_id=pos.id if pos else None, order_id=pos.order_id if pos else None,
                          signal_id=pos.signal_id if pos else None, bot_id=pos.bot_id if pos else None,
                          strategy_id=pos.strategy_id if pos else None, strategy_version_id=pos.strategy_version_id if pos else None,
                          broker_deal_id=deal.deal_id, symbol=(sym.name if sym else str(deal.symbol_id)),
                          side=pos.side if pos else ("SELL" if deal.side == "BUY" else "BUY"),
                          volume_lots=lots, entry_price=deal.entry_price, exit_price=deal.price,
                          gross_pnl=deal.gross_profit, commission=deal.close_commission, swap=deal.swap, net_pnl=net,
                          opened_at=pos.opened_at if pos else None, closed_at=_ts(deal.exec_ts_ms) or utcnow(), close_reason="CLOSED")
            s.add(trade)
            try:
                await s.flush()
            except IntegrityError:
                return None   # deal already recorded
            if pos is not None:
                pos.realized_pnl = (pos.realized_pnl or 0.0) + net
                remaining = max(0.0, pos.volume_lots - lots)
                if fully_closed or remaining <= 1e-9:
                    pos.status, pos.closed_at, pos.unrealized_pnl, pos.volume_lots = "CLOSED", utcnow(), 0.0, pos.volume_lots
                else:
                    pos.volume_lots = remaining
            tid, symname = trade.id, trade.symbol
        await self.audit.record(action="TRADE_CLOSED", target=tid, new_state={"symbol": symname, "net_pnl": net, "deal": deal.deal_id})
        if self.risk:
            await self.risk.on_trade_closed(account_id, net)
        await self.notifier.notify("TRADE_CLOSED", f"Trade closed {symname}: net {net:+.2f}", Severity.INFO if net >= 0 else Severity.NOTICE,
                                   dedup_key=f"trade:{tid}", throttle_seconds=0)
        if pos is not None:
            await self._emit(account_id, "CLOSED", pos)
        return trade

    # ---- user / system actions -------------------------------------------------------------------
    async def modify_sltp(self, position_id: str, sl: float | None, tp: float | None, user_id: int | None) -> Position:
        pos = await self.get(position_id)
        if pos.status != "OPEN":
            raise ValidationFailed("Position is not open")
        if sl is None and tp is None:
            raise ValidationFailed("Provide a stop loss and/or take profit")
        for v in (sl, tp):
            if v is not None and v <= 0:
                raise ValidationFailed("Prices must be positive")
        newsl = sl if sl is not None else pos.stop_loss
        newtp = tp if tp is not None else pos.take_profit
        try:
            await self.gateway.amend_sltp(pos.account_id, pos.broker_position_id, newsl, newtp)
        except CTraderError as exc:
            await self.audit.record(action="POSITION_MODIFY", user_id=user_id, target=pos.id, result="FAILED", error=str(exc)[:200])
            raise
        async with self.db.session() as s:
            row = await s.get(Position, pos.id)
            prev = {"sl": row.stop_loss, "tp": row.take_profit}
            row.stop_loss, row.take_profit = newsl, newtp
        await self.audit.record(action="POSITION_MODIFY", user_id=user_id, target=pos.id, previous_state=prev, new_state={"sl": newsl, "tp": newtp})
        return await self.get(pos.id)

    async def close(self, position_id: str, user_id: int | None, lots: float | None = None, reason: str = "manual") -> None:
        pos = await self.get(position_id)
        if pos.status != "OPEN":
            raise ValidationFailed("Position is not open")
        sym = await self._sym(pos.account_id, pos.symbol_id or 0)
        if sym is None:
            raise NotFoundError("Symbol info unavailable (account not connected?)")
        want = pos.volume_lots if lots is None else lots
        if want <= 0 or want > pos.volume_lots + 1e-9:
            raise ValidationFailed(f"Volume must be between 0 and {pos.volume_lots:g} lots")
        vol = sym.lots_to_protocol(want) if want < pos.volume_lots - 1e-9 else sym.lots_to_protocol(pos.volume_lots)
        if vol <= 0 or (want < pos.volume_lots - 1e-9 and vol < sym.min_volume):
            raise ValidationFailed(f"Volume below minimum ({sym.min_lots:g} lots)")
        try:
            evt = await self.gateway.close_position(pos.account_id, pos.broker_position_id, vol)
        except CTraderError as exc:
            await self.audit.record(action="POSITION_CLOSE", user_id=user_id, target=pos.id, result="FAILED", error=str(exc)[:200])
            raise
        await self.audit.record(action="POSITION_CLOSE", user_id=user_id, target=pos.id, new_state={"lots": want, "reason": reason, "partial": lots is not None and want < pos.volume_lots})
        if evt is not None:
            await self.apply_execution(pos.account_id, evt, None)

    async def close_all(self, account_id: str | None, user_id: int | None, reason: str = "close_all") -> dict[str, int]:
        ok = failed = 0
        for pos in await self.list_open(account_id, limit=1000):
            if not self.gateway.is_connected(pos.account_id):
                failed += 1
                continue
            try:
                await self.close(pos.id, user_id, reason=reason)
                ok += 1
            except Exception as exc:
                failed += 1
                log.error("close_all: failed %s: %s", pos.id, str(exc)[:120])
        return {"closed": ok, "failed": failed}

    async def close_by_bot_symbol(self, bot_id: str, symbol: str, user_id: int | None = None, side: str | None = None) -> int:
        n = 0
        for pos in await self.list_open():
            if pos.bot_id == bot_id and pos.symbol.upper() == symbol.upper() and (side is None or pos.side == side):
                await self.close(pos.id, user_id, reason="strategy_close")
                n += 1
        return n
