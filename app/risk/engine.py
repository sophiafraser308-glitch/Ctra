"""Risk Engine — the only authority that may approve an order. Every rejection has a machine-readable code."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.config import Settings
from app.core.enums import ACTIVE_ORDER_STATUSES, Category, LockScope, MarketDataStatus, OrderStatus, RiskReason
from app.core.utils import next_utc_midnight, utc_day_start, utcnow
from app.ctrader.gateway import CTraderGateway
from app.ctrader.types import SymbolInfo
from app.database import Database
from app.logging import get_logger
from app.market_data.service import MarketDataService
from app.models import Order, Position, RiskProfile, RiskState, TradingAccount
from app.risk.locks import LockService
from app.schemas.trading import RiskDecision, SignalIn
from app.services.settings_service import SystemSettingsService

log = get_logger(Category.RISK)


# ---- pure helpers (unit-tested) -------------------------------------------------
def session_allowed(sessions: list[str], now: datetime) -> bool:
    """Sessions are UTC 'HH:MM-HH:MM' windows (may wrap midnight). Empty list = always allowed."""
    if not sessions:
        return True
    cur = now.hour * 60 + now.minute
    for w in sessions:
        a, b = w.split("-")
        s = int(a[:2]) * 60 + int(a[3:])
        e = int(b[:2]) * 60 + int(b[3:])
        if (s <= e and s <= cur < e) or (s > e and (cur >= s or cur < e)):
            return True
    return False


def compute_lots(*, balance: float, risk_pct: float, sl_pips: float, pip_value_per_lot: float,
                 min_lots: float, step_lots: float, max_lots: float) -> float:
    """Lots so that hitting the stop loses ~risk_pct of balance; rounded DOWN to the step and clamped."""
    if sl_pips <= 0 or pip_value_per_lot <= 0 or balance <= 0:
        return 0.0
    raw = (balance * risk_pct / 100.0) / (sl_pips * pip_value_per_lot)
    step = step_lots if step_lots > 0 else 0.01
    lots = int(raw / step + 1e-9) * step
    lots = min(lots, max_lots)
    return round(lots, 8) if lots >= min_lots else 0.0


class RiskEngine:
    def __init__(self, db: Database, settings: Settings, sys_settings: SystemSettingsService, locks: LockService,
                 gateway: CTraderGateway, market: MarketDataService) -> None:
        self.db, self.s, self.sys, self.locks, self.gateway, self.market = db, settings, sys_settings, locks, gateway, market

    # ---- profile / state ------------------------------------------------------------------
    async def profile_for(self, account: TradingAccount, profile_id: str | None = None) -> RiskProfile:
        async with self.db.session() as s:
            p = await s.get(RiskProfile, profile_id or account.risk_profile_id) if (profile_id or account.risk_profile_id) else None
            if p is None:
                p = (await s.execute(select(RiskProfile).where(RiskProfile.is_default.is_(True)))).scalars().first()
            if p is None:
                p = RiskProfile(name="Default", is_default=True)
                s.add(p)
                await s.flush()
            return p

    async def _state(self, s, account_id: str) -> RiskState:
        st = await s.get(RiskState, account_id)
        today = utcnow().strftime("%Y-%m-%d")
        if st is None:
            st = RiskState(account_id=account_id, day=today)
            s.add(st)
            await s.flush()
        if st.day != today:   # daily rollover
            st.day, st.day_start_balance, st.realized_today, st.trades_today = today, None, 0.0, 0
        return st

    async def get_state(self, account_id: str) -> RiskState:
        async with self.db.session() as s:
            return await self._state(s, account_id)

    async def update_account_state(self, account_id: str, *, balance: float, equity: float) -> None:
        async with self.db.session() as s:
            st = await self._state(s, account_id)
            if st.day_start_balance is None:
                st.day_start_balance = balance - (st.realized_today or 0.0)
            if st.peak_equity is None or equity > st.peak_equity:
                st.peak_equity = equity
            acc = await s.get(TradingAccount, account_id)
        if acc:
            await self.check_limits(acc)

    async def on_trade_closed(self, account_id: str, net_pnl: float) -> None:
        async with self.db.session() as s:
            st = await self._state(s, account_id)
            st.realized_today = (st.realized_today or 0.0) + net_pnl
            st.consecutive_losses = (st.consecutive_losses + 1) if net_pnl < 0 else (0 if net_pnl > 0 else st.consecutive_losses)
            acc = await s.get(TradingAccount, account_id)
        if acc:
            await self.check_limits(acc)

    async def check_limits(self, acc: TradingAccount) -> RiskReason | None:
        """Create persistent locks when thresholds are breached (independent of any signal)."""
        profile = await self.profile_for(acc)
        st = await self.get_state(acc.id)
        expires = next_utc_midnight() if profile.lock_policy == "NEXT_DAY" else None
        start = st.day_start_balance or acc.balance
        if start:
            loss = -((st.realized_today or 0.0) + min(0.0, acc.unrealized_pnl or 0.0))
            if loss > 0 and loss / start * 100 >= profile.max_daily_loss_pct:
                await self.locks.create(LockScope.ACCOUNT, acc.id, RiskReason.DAILY_LOSS.value,
                                        f"Daily loss {loss:.2f} ({loss / start * 100:.2f}% >= {profile.max_daily_loss_pct}%)", expires)
                return RiskReason.DAILY_LOSS
        if st.peak_equity and acc.equity is not None:
            dd = (st.peak_equity - acc.equity) / st.peak_equity * 100
            if dd >= profile.max_drawdown_pct:
                await self.locks.create(LockScope.ACCOUNT, acc.id, RiskReason.DRAWDOWN.value,
                                        f"Drawdown {dd:.2f}% >= {profile.max_drawdown_pct}%", expires)
                return RiskReason.DRAWDOWN
        if st.consecutive_losses >= profile.max_consecutive_losses:
            await self.locks.create(LockScope.ACCOUNT, acc.id, RiskReason.CONSECUTIVE_LOSSES.value,
                                    f"{st.consecutive_losses} consecutive losses >= {profile.max_consecutive_losses}", expires)
            return RiskReason.CONSECUTIVE_LOSSES
        return None

    async def reset_consecutive(self, account_id: str) -> None:
        async with self.db.session() as s:
            st = await self._state(s, account_id)
            st.consecutive_losses = 0

    # ---- evaluation -------------------------------------------------------------------------
    def _rej(self, reason: RiskReason, **detail: Any) -> RiskDecision:
        return RiskDecision(approved=False, reason=reason.value, detail=detail)

    async def pip_value_per_lot(self, account: TradingAccount, sym: SymbolInfo) -> float | None:
        sess = self.gateway.sessions.get(account.id)
        if not sess or not account.currency:
            return None
        quote_ccy = sess.assets.get(sym.quote_asset_id or -1)
        if not quote_ccy:
            return None
        units = sym.lot_size / 100.0
        rate = await self.market.conversion_rate(account.id, quote_ccy, account.currency)
        if rate is None:
            return None
        return units * sym.pip_size * rate

    async def evaluate(self, *, account: TradingAccount, signal: SignalIn, sym: SymbolInfo, bot_id: str | None,
                       strategy_id: str | None, profile_id: str | None = None) -> RiskDecision:
        now = utcnow()
        profile = await self.profile_for(account, profile_id)
        # 1. global flags
        if self.sys.new_trading_disabled:
            return self._rej(RiskReason.TRADING_DISABLED, why="emergency: new trading disabled")
        if account.environment == "LIVE" and not self.sys.live_allowed:
            return self._rej(RiskReason.LIVE_DISABLED)
        if not profile.trading_enabled or not account.trading_enabled:
            return self._rej(RiskReason.PERMISSION, why="trading disabled by profile/account")
        # 2. locks
        locks = await self.locks.active(account_id=account.id, bot_id=bot_id, strategy_id=strategy_id)
        if locks:
            return self._rej(RiskReason.LOCKED, locks=[f"{l.scope_type}:{l.reason}" for l in locks])
        # 3. connectivity
        if not self.gateway.is_connected(account.id):
            return self._rej(RiskReason.NOT_CONNECTED)
        scope = None
        try:
            scope = self.gateway.session(account.id)
        except Exception:
            return self._rej(RiskReason.NOT_CONNECTED)
        if account.balance is None or account.equity is None:
            return self._rej(RiskReason.NO_ACCOUNT_DATA)
        # 4. symbol & session
        if profile.allowed_symbols and signal.symbol not in [x.upper() for x in profile.allowed_symbols]:
            return self._rej(RiskReason.SYMBOL_NOT_ALLOWED, symbol=signal.symbol)
        if not session_allowed(profile.allowed_sessions or [], now):
            return self._rej(RiskReason.SESSION, sessions=profile.allowed_sessions)
        # 5. market data freshness & spread
        if profile.require_fresh_data:
            st = self.market.status(account.id, sym.symbol_id)
            if st != MarketDataStatus.FRESH:
                return self._rej(RiskReason.STALE_DATA, status=st.value, age=self.market.age_seconds(account.id, sym.symbol_id))
        spread = self.market.spread_pips(account.id, sym)
        if spread is not None and profile.max_spread_pips > 0 and spread > profile.max_spread_pips:
            return self._rej(RiskReason.SPREAD, spread_pips=round(spread, 2), limit=profile.max_spread_pips)
        # 6-11. counters
        async with self.db.session() as s:
            st = await self._state(s, account.id)
            day0 = utc_day_start(now)
            trades = (await s.execute(select(func.count()).select_from(Order).where(
                Order.account_id == account.id, Order.created_at >= day0,
                Order.status.in_([OrderStatus.FILLED.value, OrderStatus.PARTIALLY_FILLED.value, OrderStatus.SUBMITTED.value,
                                  OrderStatus.ACCEPTED.value, OrderStatus.PROCESSING.value, OrderStatus.UNKNOWN.value])))).scalar_one()
            pos = (await s.execute(select(Position).where(Position.account_id == account.id, Position.status == "OPEN"))).scalars().all()
            pending_vol = (await s.execute(select(func.coalesce(func.sum(Order.volume_lots), 0.0)).where(
                Order.account_id == account.id, Order.status.in_([x.value for x in ACTIVE_ORDER_STATUSES])))).scalar_one()
        if trades >= profile.max_trades_per_day:
            return self._rej(RiskReason.MAX_TRADES_DAY, trades=trades, limit=profile.max_trades_per_day)
        if len(pos) >= profile.max_open_positions:
            return self._rej(RiskReason.MAX_OPEN_POSITIONS, open=len(pos), limit=profile.max_open_positions)
        lim = await self.check_limits(account)
        if lim is not None:
            return self._rej(lim)
        if st.consecutive_losses >= profile.max_consecutive_losses:
            return self._rej(RiskReason.CONSECUTIVE_LOSSES, losses=st.consecutive_losses)
        # 12. sizing
        if not signal.stop_loss_pips:
            if profile.require_stop_loss:
                return self._rej(RiskReason.NO_STOP_LOSS)
            return self._rej(RiskReason.NO_STOP_LOSS, why="cannot size position without a stop distance")
        pv = await self.pip_value_per_lot(account, sym)
        if pv is None:
            return self._rej(RiskReason.NO_CONVERSION, deposit_currency=account.currency)
        lots = compute_lots(balance=account.balance, risk_pct=profile.risk_per_trade_pct, sl_pips=signal.stop_loss_pips,
                            pip_value_per_lot=pv, min_lots=sym.min_lots, step_lots=sym.step_lots,
                            max_lots=min(profile.max_position_lots, sym.max_lots))
        if lots <= 0:
            return self._rej(RiskReason.MIN_VOLUME, pip_value=round(pv, 4), min_lots=sym.min_lots)
        # 13. exposure
        total = sum(p.volume_lots for p in pos) + pending_vol
        if total + lots > profile.max_total_exposure_lots + 1e-9:
            return self._rej(RiskReason.MAX_EXPOSURE, current=round(total, 2), new=lots, limit=profile.max_total_exposure_lots)
        sym_exp = sum(p.volume_lots for p in pos if p.symbol == signal.symbol)
        if sym_exp + lots > profile.max_symbol_exposure_lots + 1e-9:
            return self._rej(RiskReason.SYMBOL_EXPOSURE, current=round(sym_exp, 2), new=lots, limit=profile.max_symbol_exposure_lots)
        equity = account.equity or 0.0
        if equity > 0 and account.used_margin is not None:
            used_pct = account.used_margin / equity * 100
            if used_pct >= profile.max_account_exposure_pct:
                return self._rej(RiskReason.ACCOUNT_EXPOSURE, used_pct=round(used_pct, 1), limit=profile.max_account_exposure_pct)
        # 14. free margin
        if equity > 0 and account.free_margin is not None:
            free_pct = account.free_margin / equity * 100
            if free_pct < profile.min_free_margin_pct:
                return self._rej(RiskReason.FREE_MARGIN, free_pct=round(free_pct, 1), limit=profile.min_free_margin_pct)
        return RiskDecision(approved=True, reason=RiskReason.APPROVED.value, lots=lots,
                            detail={"pip_value_per_lot": round(pv, 4), "risk_pct": profile.risk_per_trade_pct,
                                    "spread_pips": None if spread is None else round(spread, 2)})
