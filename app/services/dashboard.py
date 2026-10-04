from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app import __version__
from app.core.enums import AccountStatus
from app.core.utils import utcnow
from app.models import LogEntry, Position, RiskState, TradingAccount


class DashboardService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    async def snapshot(self) -> dict[str, Any]:
        c = self.ctx
        accounts = await c.accounts.list()
        bots = await c.bots.counts()
        locks = await c.locks.list_active()
        async with c.db.session() as s:
            open_pos = (await s.execute(select(func.count()).select_from(Position).where(Position.status == "OPEN"))).scalar_one()
            states = (await s.execute(select(RiskState))).scalars().all()
            last_err = (await s.execute(select(LogEntry).where(LogEntry.severity.in_(["ERROR", "CRITICAL"])).order_by(LogEntry.id.desc()).limit(1))).scalar_one_or_none()
        pending = await c.orders.count(["ACCEPTED", "SUBMITTED", "PROCESSING", "PENDING"])
        unknown = await c.orders.count(["UNKNOWN"])
        per_ccy: dict[str, dict[str, float]] = {}
        for a in accounts:
            d = per_ccy.setdefault(a.currency or "?", {"balance": 0.0, "equity": 0.0, "free": 0.0, "used": 0.0, "unreal": 0.0})
            d["balance"] += a.balance or 0; d["equity"] += a.equity or 0; d["free"] += a.free_margin or 0
            d["used"] += a.used_margin or 0; d["unreal"] += a.unrealized_pnl or 0
        today_realized = sum(st.realized_today or 0 for st in states if st.day == utcnow().strftime("%Y-%m-%d"))
        max_dd = 0.0
        for a in accounts:
            st = next((x for x in states if x.account_id == a.id), None)
            if st and st.peak_equity and a.equity is not None:
                max_dd = max(max_dd, (st.peak_equity - a.equity) / st.peak_equity * 100)
        hb = {h.component: h for h in c.heartbeat.snapshot()}
        tg = hb.get("telegram")
        return {
            "version": __version__, "env": c.settings.app_env, "live_allowed": c.sys_settings.live_allowed,
            "new_trading_disabled": c.sys_settings.new_trading_disabled,
            "accounts": {"total": len(accounts), "demo": sum(a.environment == "DEMO" for a in accounts),
                         "live": sum(a.environment == "LIVE" for a in accounts),
                         "connected": sum(a.status == AccountStatus.CONNECTED.value for a in accounts)},
            "bots": bots, "open_positions": open_pos, "pending_orders": pending, "unknown_orders": unknown,
            "money": per_ccy, "realized_today": today_realized, "max_drawdown_pct": max_dd,
            "locks": [(l.scope_type, l.scope_id, l.reason) for l in locks],
            "components": {k: (v.status, v.last_error) for k, v in hb.items() if not k.startswith("bot:")},
            "telegram": tg.status if tg else "?", "db": hb["database"].status if "database" in hb else "?",
            "ctrader": c.gateway.info()["managers"], "last_heartbeat": c.heartbeat.last_beat_at,
            "last_sync": max([a.last_sync_at for a in accounts if a.last_sync_at], default=None),
            "last_error": (last_err.ts, last_err.message[:120]) if last_err else None,
            "watchdog": (c.watchdog.last_run_at, len(c.watchdog.last_findings)),
        }
