from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_id, utcnow
from app.database.base import Base, Money, TimestampMixin, UTCDateTime


class RiskProfile(Base, TimestampMixin):
    __tablename__ = "risk_profiles"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=0.5)
    max_position_lots: Mapped[float] = mapped_column(Float, default=1.0)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=3)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=10)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=3.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=10.0)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=4)
    max_total_exposure_lots: Mapped[float] = mapped_column(Float, default=3.0)
    max_symbol_exposure_lots: Mapped[float] = mapped_column(Float, default=1.5)
    max_account_exposure_pct: Mapped[float] = mapped_column(Float, default=50.0)  # used margin / equity
    max_spread_pips: Mapped[float] = mapped_column(Float, default=3.0)
    min_free_margin_pct: Mapped[float] = mapped_column(Float, default=50.0)       # free margin / equity
    require_stop_loss: Mapped[bool] = mapped_column(Boolean, default=True)
    require_fresh_data: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_symbols: Mapped[list[Any]] = mapped_column(JSON, default=list)        # [] = all
    allowed_sessions: Mapped[list[Any]] = mapped_column(JSON, default=list)       # ["07:00-17:00"] UTC, [] = always
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    lock_policy: Mapped[str] = mapped_column(String(16), default="MANUAL")        # MANUAL | NEXT_DAY
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class TradingAccount(Base, TimestampMixin):
    __tablename__ = "trading_accounts"
    __table_args__ = (UniqueConstraint("ctid_trader_account_id", "environment", name="uq_account_ctid_env"),)
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(8), index=True)     # DEMO | LIVE
    ctid_trader_account_id: Mapped[int] = mapped_column(BigInteger)
    trader_login: Mapped[int | None] = mapped_column(BigInteger)
    broker: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(8))
    leverage: Mapped[float | None] = mapped_column(Float)
    money_digits: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(16), default="DISCONNECTED", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)           # connect on startup
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)   # per-account trading permission
    balance: Mapped[float | None] = mapped_column(Money)
    equity: Mapped[float | None] = mapped_column(Money)
    used_margin: Mapped[float | None] = mapped_column(Money)
    free_margin: Mapped[float | None] = mapped_column(Money)
    unrealized_pnl: Mapped[float | None] = mapped_column(Money)
    risk_profile_id: Mapped[str | None] = mapped_column(String(16), ForeignKey("risk_profiles.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)


class CredentialMetadata(Base, TimestampMixin):
    """Encrypted OAuth tokens + non-secret token metadata for one account."""
    __tablename__ = "credential_metadata"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(String(16), ForeignKey("trading_accounts.id", ondelete="CASCADE"), unique=True)
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[str | None] = mapped_column(String(16))
    scope: Mapped[str | None] = mapped_column(String(16))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    refreshed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default="VALID")   # VALID | REFRESH_FAILED | REVOKED
    last_error: Mapped[str | None] = mapped_column(Text)


class OAuthState(Base):
    """Pending OAuth authorisation (state nonce -> Telegram user). Tokens kept encrypted until account selection."""
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    account_name: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(8))
    scope: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING | AUTHORIZED | DONE | FAILED | EXPIRED
    tokens_enc: Mapped[str | None] = mapped_column(Text)               # encrypted JSON
    accounts_json: Mapped[list[Any] | None] = mapped_column(JSON)      # non-secret account list from broker
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ConnectionEvent(Base):
    __tablename__ = "connection_events"
    __table_args__ = (Index("ix_conn_events_acc_ts", "account_id", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str | None] = mapped_column(String(16), index=True)
    environment: Mapped[str | None] = mapped_column(String(8))
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(Text)
