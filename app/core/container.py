"""AppContext: wires every service together (explicit dependency injection, no globals)."""
from __future__ import annotations

from typing import Any

from app.accounts.service import AccountService
from app.audit import AuditService
from app.bots.manager import BotManager
from app.bots.pipeline import SignalPipeline
from app.config import Settings
from app.ctrader.authentication import OAuthClient, TokenService
from app.ctrader.gateway import CTraderGateway
from app.database import Database
from app.market_data.service import MarketDataService
from app.notifications.service import Notifier
from app.orders.manager import OrderManager
from app.positions.service import PositionService
from app.reconciliation.service import ReconciliationService
from app.risk.engine import RiskEngine
from app.risk.locks import LockService
from app.security.crypto import CryptoService
from app.security.users import UserService
from app.services.command_service import CommandService
from app.services.dashboard import DashboardService
from app.services.emergency import EmergencyService
from app.services.logs_service import LogQueryService
from app.services.settings_service import SystemSettingsService
from app.strategies.manager import StrategyService
from app.watchdog.service import WatchdogService
from app.workers.heartbeat import HeartbeatService


class AppContext:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings, self.db = settings, db
        self.bot_api: Any = None                      # aiogram Bot (set at startup)
        self.crypto = CryptoService(settings.encryption_key.get_secret_value())
        self.audit = AuditService(db)
        self.sys_settings = SystemSettingsService(db, settings)
        self.heartbeat = HeartbeatService(db, settings)
        self.notifier = Notifier(db, settings, self.sys_settings)
        self.users = UserService(db, settings, self.audit)
        self.commands = CommandService(db)
        self.logs = LogQueryService(db)
        self.oauth = OAuthClient(settings)
        self.tokens = TokenService(db, self.crypto, self.oauth, settings)
        self.gateway = CTraderGateway(settings, self.tokens)
        self.market = MarketDataService(settings, self.gateway, self.heartbeat, self.notifier)
        self.locks = LockService(db, self.audit, self.notifier)
        self.risk = RiskEngine(db, settings, self.sys_settings, self.locks, self.gateway, self.market)
        self.positions = PositionService(db, self.gateway, self.audit, self.notifier)
        self.orders = OrderManager(db, settings, self.sys_settings, self.gateway, self.audit, self.notifier, self.heartbeat, self.positions)
        self.recon = ReconciliationService(db, settings, self.gateway, self.positions, self.orders, self.audit, self.notifier, self.heartbeat)
        self.accounts = AccountService(db, settings, self.audit, self.notifier, self.gateway, self.tokens, self.oauth, self.crypto, self.heartbeat)
        self.strategies = StrategyService(db, settings, self.audit)
        self.pipeline = SignalPipeline(db, self.gateway, self.risk, self.orders, self.positions, self.heartbeat)
        self.bots = BotManager(self)
        self.watchdog = WatchdogService(self)
        self.dashboard = DashboardService(self)
        self.emergency = EmergencyService(self)
        # late binding of circular collaborators
        self.positions.risk = self.risk
        self.positions.market = self.market
        self.orders.recon = self.recon
        self.orders.risk = self.risk
        self.recon.accounts = self.accounts
        self.accounts.recon = self.recon
        self.accounts.positions = self.positions
        self.accounts.risk = self.risk
        self.accounts.bots = self.bots
        self.locks.on_lock.append(self.bots.on_lock)
        self.locks.on_release.append(self.bots.on_release)
