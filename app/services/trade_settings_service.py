"""Persistence + audit for the shared trade settings (see app/core/trade_settings.py)."""
from __future__ import annotations

from typing import Any

from app.audit import AuditService
from app.core import trade_settings as T
from app.services.settings_service import SystemSettingsService


class TradeSettingsService:
    def __init__(self, sys_settings: SystemSettingsService, audit: AuditService) -> None:
        self.sys, self.audit = sys_settings, audit

    # synchronous read from the in-memory cache: safe to call on every signal / backtest
    def get(self) -> dict[str, Any]:
        return T.normalize(self.sys.get(T.KEY))

    def effective(self, tf: str | None) -> dict[str, Any]:
        return T.effective(self.get(), tf)

    async def _save(self, new: dict[str, Any], user_id: int | None, what: str, old: dict[str, Any]) -> dict[str, Any]:
        await self.sys.set(T.KEY, new, user_id)
        await self.audit.record(action="TRADE_SETTINGS", user_id=user_id, target=what, previous_state=old, new_state=new)
        return new

    async def set_level(self, kind: str, tf: str | None, value: float | None, user_id: int | None) -> dict[str, Any]:
        old = self.get()
        return await self._save(T.with_value(old, kind, tf, value), user_id, f"{kind}:{tf or 'all'}", old)

    async def set_lot(self, lots: float, user_id: int | None) -> dict[str, Any]:
        old = self.get()
        return await self._save({**old, "lot_size": float(lots)}, user_id, "lot", old)

    async def set_multi(self, on: bool, user_id: int | None) -> dict[str, Any]:
        old = self.get()
        return await self._save({**old, "multi_tf": bool(on)}, user_id, "multi_tf", old)

    async def reset(self, user_id: int | None) -> dict[str, Any]:
        old = self.get()
        return await self._save(T.normalize(None), user_id, "reset", old)
