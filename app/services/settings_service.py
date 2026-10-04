"""Runtime system settings persisted in DB (kill-switches, live gate, notification prefs)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.exceptions import SafetyError
from app.core.utils import utcnow
from app.config import Settings
from app.database import Database
from app.models import SystemSetting

DEFAULTS: dict[str, Any] = {
    "live_trading_enabled": False,     # runtime switch; also needs env LIVE_TRADING_ENABLED=true
    "new_trading_disabled": False,     # emergency kill-switch for new orders
    "notifications_muted": False,
    "muted_notification_kinds": [],
}


class SystemSettingsService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._cache: dict[str, Any] = {}

    async def load(self) -> None:
        async with self.db.session() as s:
            for row in (await s.execute(select(SystemSetting))).scalars():
                self._cache[row.key] = row.value

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._cache:
            return self._cache[key]
        return DEFAULTS.get(key, default)

    async def set(self, key: str, value: Any, user_id: int | None = None) -> None:
        async with self.db.session() as s:
            row = await s.get(SystemSetting, key)
            if row:
                row.value, row.updated_by, row.updated_at = value, user_id, utcnow()
            else:
                s.add(SystemSetting(key=key, value=value, updated_by=user_id))
        self._cache[key] = value

    # ---- safety gates -------------------------------------------------
    @property
    def live_allowed(self) -> bool:
        """Both the env hard gate and the runtime switch must be on."""
        return bool(self.settings.live_trading_enabled and self.get("live_trading_enabled"))

    @property
    def new_trading_disabled(self) -> bool:
        return bool(self.get("new_trading_disabled"))

    async def set_live_trading(self, enabled: bool, user_id: int | None) -> None:
        if enabled and not self.settings.live_trading_enabled:
            raise SafetyError("LIVE_TRADING_ENABLED=false in environment (hard gate). Change the env var and restart first.")
        await self.set("live_trading_enabled", enabled, user_id)
