from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_id, utcnow
from app.database.base import Base, Money, TimestampMixin, UTCDateTime


class Bot(Base, TimestampMixin):
    __tablename__ = "bots"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(16), ForeignKey("trading_accounts.id"), index=True)
    environment: Mapped[str] = mapped_column(String(8))
    strategy_id: Mapped[str] = mapped_column(String(16), ForeignKey("strategies.id"))
    strategy_version_id: Mapped[str] = mapped_column(String(16), ForeignKey("strategy_versions.id"))
    symbols: Mapped[list[Any]] = mapped_column(JSON, default=list)
    timeframes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)     # overrides of strategy defaults
    risk_profile_id: Mapped[str | None] = mapped_column(String(16), ForeignKey("risk_profiles.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="CREATED", index=True)
    desired_state: Mapped[str] = mapped_column(String(16), default="STOPPED")  # used for restart recovery
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_signal_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_order_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    restart_count: Mapped[int] = mapped_column(Integer, default=0)
    lock_reason: Mapped[str | None] = mapped_column(String(128))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    correlation_id: Mapped[str] = mapped_column(String(32), index=True)
    bot_id: Mapped[str | None] = mapped_column(String(16), ForeignKey("bots.id", ondelete="SET NULL"), index=True)
    account_id: Mapped[str] = mapped_column(String(16), ForeignKey("trading_accounts.id"), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(16))
    strategy_version_id: Mapped[str | None] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))                 # BUY | SELL | CLOSE
    order_type: Mapped[str] = mapped_column(String(8), default="MARKET")
    price: Mapped[float | None] = mapped_column(Float)
    stop_loss_pips: Mapped[float | None] = mapped_column(Float)
    take_profit_pips: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    risk_decision: Mapped[str | None] = mapped_column(String(12))     # APPROVED | REJECTED
    risk_reason: Mapped[str | None] = mapped_column(String(48))
    risk_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    approved_lots: Mapped[float | None] = mapped_column(Float)
    order_id: Mapped[str | None] = mapped_column(String(16))


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_acc_status", "account_id", "status"),)
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(64), unique=True)       # idempotency key
    client_order_id: Mapped[str] = mapped_column(String(50), unique=True)  # sent to broker (clientOrderId)
    correlation_id: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[str] = mapped_column(String(16), ForeignKey("trading_accounts.id"), index=True)
    bot_id: Mapped[str | None] = mapped_column(String(16), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(16))
    strategy_version_id: Mapped[str | None] = mapped_column(String(16))
    signal_id: Mapped[str | None] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    symbol_id: Mapped[int | None] = mapped_column(BigInteger)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(8), default="MARKET")
    volume_lots: Mapped[float] = mapped_column(Float)
    protocol_volume: Mapped[int | None] = mapped_column(BigInteger)
    price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    stop_loss_pips: Mapped[float | None] = mapped_column(Float)
    take_profit_pips: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    broker_order_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    broker_position_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    filled_volume_lots: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float)
    reject_reason: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    retry_of: Mapped[str | None] = mapped_column(String(16))
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Position(Base, TimestampMixin):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "broker_position_id", name="uq_position_broker"),
                      Index("ix_positions_acc_status", "account_id", "status"))
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(String(16), ForeignKey("trading_accounts.id"), index=True)
    broker_position_id: Mapped[int] = mapped_column(BigInteger)
    symbol: Mapped[str] = mapped_column(String(32))
    symbol_id: Mapped[int | None] = mapped_column(BigInteger)
    side: Mapped[str] = mapped_column(String(8))
    volume_lots: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float)
    current_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Money, default=0.0)
    unrealized_pnl: Mapped[float | None] = mapped_column(Money)
    swap: Mapped[float] = mapped_column(Money, default=0.0)
    commission: Mapped[float] = mapped_column(Money, default=0.0)
    used_margin: Mapped[float | None] = mapped_column(Money)
    strategy_id: Mapped[str | None] = mapped_column(String(16))
    strategy_version_id: Mapped[str | None] = mapped_column(String(16))
    bot_id: Mapped[str | None] = mapped_column(String(16), index=True)
    order_id: Mapped[str | None] = mapped_column(String(16))
    signal_id: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(10), default="BOT")       # BOT | EXTERNAL
    status: Mapped[str] = mapped_column(String(8), default="OPEN", index=True)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Trade(Base):
    """A closed round-trip (or partial close) result — the end of the traceability chain."""
    __tablename__ = "trades"
    __table_args__ = (UniqueConstraint("account_id", "broker_deal_id", name="uq_trade_deal"),)
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(String(16), ForeignKey("trading_accounts.id"), index=True)
    position_id: Mapped[str | None] = mapped_column(String(16), index=True)
    order_id: Mapped[str | None] = mapped_column(String(16))
    signal_id: Mapped[str | None] = mapped_column(String(16))
    bot_id: Mapped[str | None] = mapped_column(String(16), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(16), index=True)
    strategy_version_id: Mapped[str | None] = mapped_column(String(16))
    broker_deal_id: Mapped[int | None] = mapped_column(BigInteger)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    volume_lots: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float)
    gross_pnl: Mapped[float] = mapped_column(Money, default=0.0)
    commission: Mapped[float] = mapped_column(Money, default=0.0)
    swap: Mapped[float] = mapped_column(Money, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Money, default=0.0)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    close_reason: Mapped[str | None] = mapped_column(String(32))


class RiskLock(Base):
    __tablename__ = "risk_locks"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    scope_type: Mapped[str] = mapped_column(String(10), index=True)
    scope_id: Mapped[str | None] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(String(48))
    detail: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    released_by: Mapped[int | None] = mapped_column(BigInteger)


class RiskState(Base):
    """Persisted per-account risk counters so locks/limits survive restarts."""
    __tablename__ = "risk_states"
    account_id: Mapped[str] = mapped_column(String(16), ForeignKey("trading_accounts.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), default="")
    day_start_balance: Mapped[float | None] = mapped_column(Money)
    peak_equity: Mapped[float | None] = mapped_column(Money)
    trades_today: Mapped[int] = mapped_column(Integer, default=0)
    realized_today: Mapped[float] = mapped_column(Money, default=0.0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ReconciliationEvent(Base):
    __tablename__ = "reconciliation_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(16), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    trigger: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(24))        # ORPHAN_POSITION | PHANTOM_POSITION | ... | CLEAN
    severity: Mapped[str] = mapped_column(String(10), default="INFO")
    local_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    broker_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    action: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=True)
