from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_id, utcnow
from app.database.base import Base, UTCDateTime


class LogEntry(Base):
    __tablename__ = "log_entries"
    __table_args__ = (Index("ix_log_cat_ts", "category", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    category: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(10), default="INFO")
    message: Mapped[str] = mapped_column(Text)
    dedup_key: Mapped[str | None] = mapped_column(String(128))
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    suppressed_count: Mapped[int] = mapped_column(Integer, default=0)


class Command(Base):
    __tablename__ = "commands"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)   # command_id
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    correlation_id: Mapped[str] = mapped_column(String(32), index=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    target_type: Mapped[str | None] = mapped_column(String(24))
    target_id: Mapped[str | None] = mapped_column(String(32))
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="RUNNING")   # RUNNING | DONE | FAILED
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(48), index=True)
    target: Mapped[str | None] = mapped_column(String(96))
    previous_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    new_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(12), default="OK")
    error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(32), index=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Worker(Base):
    __tablename__ = "workers"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), default="service")
    status: Mapped[str] = mapped_column(String(12), default="STARTING")
    pid: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_heartbeat: Mapped[datetime | None] = mapped_column(UTCDateTime)
    restarts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class Heartbeat(Base):
    """Latest heartbeat per component (upserted)."""
    __tablename__ = "heartbeats"
    component: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(12))
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    version: Mapped[str | None] = mapped_column(String(16))
    uptime_seconds: Mapped[float | None] = mapped_column(Float)
    last_operation: Mapped[str | None] = mapped_column(String(128))
    last_signal_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_order_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_ctrader_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_market_data_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
