"""Emergency controls. All are idempotent, audited, and notify admins."""
from __future__ import annotations

from typing import Any

from app.core.enums import Category, Severity
from app.logging import get_logger

log = get_logger(Category.SECURITY)


class EmergencyService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    async def _announce(self, user_id: int, action: str, detail: str) -> None:
        await self.ctx.audit.record(action=f"EMERGENCY_{action}", user_id=user_id, new_state={"detail": detail})
        await self.ctx.notifier.notify("EMERGENCY", f"🚨 EMERGENCY {action} by {user_id}: {detail}", Severity.CRITICAL, dedup_key=f"em:{action}", throttle_seconds=0)
        log.critical("EMERGENCY %s by %s: %s", action, user_id, detail)

    async def stop_all_bots(self, user_id: int) -> int:
        n = await self.ctx.bots.stop_all(user_id, reason="emergency")
        await self._announce(user_id, "STOP_ALL_BOTS", f"{n} bot(s) stopped")
        return n

    async def set_new_trading_disabled(self, user_id: int, disabled: bool) -> None:
        await self.ctx.sys_settings.set("new_trading_disabled", disabled, user_id)
        await self._announce(user_id, "DISABLE_NEW_TRADING" if disabled else "ENABLE_NEW_TRADING", f"new_trading_disabled={disabled}")

    async def close_all_positions(self, user_id: int) -> dict[str, int]:
        res = await self.ctx.positions.close_all(None, user_id, reason="emergency")
        await self._announce(user_id, "CLOSE_ALL_POSITIONS", str(res))
        return res

    async def cancel_all_pending(self, user_id: int) -> dict[str, int]:
        res = await self.ctx.orders.cancel_all_pending(None, user_id)
        await self._announce(user_id, "CANCEL_PENDING", str(res))
        return res

    async def reset_locks(self, user_id: int) -> int:
        n = await self.ctx.locks.release_all(user_id)
        for acc in await self.ctx.accounts.list():
            await self.ctx.risk.reset_consecutive(acc.id)
        await self._announce(user_id, "RESET_LOCKS", f"{n} lock(s) released")
        return n
