"""Signal pipeline: Strategy signal -> persisted Signal -> Risk Engine -> Order Manager -> cTrader."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.enums import Category, RiskReason
from app.core.exceptions import PlatformError
from app.core.utils import new_correlation_id, utcnow
from app.database import Database
from app.logging import get_logger
from app.models import Bot, Signal, TradingAccount
from app.schemas.trading import OrderIntent, SignalIn

log = get_logger(Category.SIGNAL)


class SignalPipeline:
    def __init__(self, db: Database, gateway: Any, risk: Any, orders: Any, positions: Any, hb: Any) -> None:
        self.db, self.gateway, self.risk, self.orders, self.positions, self.hb = db, gateway, risk, orders, positions, hb

    async def process(self, bot: Bot, raw_signals: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for raw in raw_signals:
            corr = new_correlation_id()
            try:
                sig = SignalIn(**raw)
            except ValidationError as exc:
                await self._store_invalid(bot, raw, corr, str(exc)[:300])
                continue
            self.hb.update(f"bot:{bot.id}", signal=True)
            async with self.db.session() as s:
                row = Signal(correlation_id=corr, bot_id=bot.id, account_id=bot.account_id, strategy_id=bot.strategy_id,
                             strategy_version_id=bot.strategy_version_id, symbol=sig.symbol, side=sig.side, order_type=sig.order_type,
                             price=sig.price, stop_loss_pips=sig.stop_loss_pips, take_profit_pips=sig.take_profit_pips,
                             payload=sig.model_dump())
                s.add(row)
                await s.flush()
                sid = row.id
                b = await s.get(Bot, bot.id)
                if b:
                    b.last_signal_at = utcnow()
                acc = await s.get(TradingAccount, bot.account_id)
            log.info("signal %s %s %s bot=%s", sig.side, sig.symbol, sid, bot.id, ctx={"corr": corr, "signal": sid})
            if sig.symbol not in [x.upper() for x in (bot.symbols or [])]:
                await self._decide(sid, "REJECTED", RiskReason.SYMBOL_NOT_ALLOWED.value, {"why": "symbol not configured for this bot"})
                continue
            try:
                if sig.side == "CLOSE":
                    n = await self.positions.close_by_bot_symbol(bot.id, sig.symbol, None)
                    await self._decide(sid, "APPROVED", "CLOSE_SIGNAL", {"closed": n})
                    out.append(sid)
                    continue
                sym = await self.gateway.get_symbol(acc.id, sig.symbol)
                dec = await self.risk.evaluate(account=acc, signal=sig, sym=sym, bot_id=bot.id, strategy_id=bot.strategy_id, profile_id=bot.risk_profile_id)
            except PlatformError as exc:
                await self._decide(sid, "REJECTED", "PIPELINE_ERROR", {"error": str(exc)[:200]})
                continue
            await self._decide(sid, "APPROVED" if dec.approved else "REJECTED", dec.reason, dec.detail, dec.lots if dec.approved else None)
            if not dec.approved:
                log.notice("signal %s rejected: %s", sid, dec.reason, ctx={"corr": corr, "detail": dec.detail})
                continue
            intent = OrderIntent(request_id=f"sig-{sid}", correlation_id=corr, account_id=acc.id, symbol=sig.symbol, side=sig.side,
                                 order_type=sig.order_type, volume_lots=dec.lots, price=sig.price, stop_loss_pips=sig.stop_loss_pips,
                                 take_profit_pips=sig.take_profit_pips, bot_id=bot.id, strategy_id=bot.strategy_id,
                                 strategy_version_id=bot.strategy_version_id, signal_id=sid, comment=f"{(bot.name or 'bot')[:20]}|{sig.comment or ''}"[:60])
            try:
                order = await self.orders.submit(intent)
                async with self.db.session() as s:
                    row = await s.get(Signal, sid)
                    row.order_id = order.id
                out.append(sid)
            except Exception as exc:
                log.error("order submit failed for signal %s: %s", sid, type(exc).__name__, exc_info=True)
        return out

    async def _decide(self, sid: str, decision: str, reason: str, detail: dict, lots: float | None = None) -> None:
        async with self.db.session() as s:
            row = await s.get(Signal, sid)
            row.risk_decision, row.risk_reason, row.risk_detail, row.approved_lots = decision, reason[:48], detail, lots

    async def _store_invalid(self, bot: Bot, raw: dict, corr: str, err: str) -> None:
        log.warning("invalid signal from bot %s: %s", bot.id, err)
        async with self.db.session() as s:
            s.add(Signal(correlation_id=corr, bot_id=bot.id, account_id=bot.account_id, strategy_id=bot.strategy_id,
                         strategy_version_id=bot.strategy_version_id, symbol=str(raw.get("symbol", "?"))[:32], side=str(raw.get("side", "?"))[:8],
                         payload={"raw": {k: str(v)[:80] for k, v in list(raw.items())[:10]}, "error": err},
                         risk_decision="REJECTED", risk_reason=RiskReason.INVALID_SIGNAL.value))
