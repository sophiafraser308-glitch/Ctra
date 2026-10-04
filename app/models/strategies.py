from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_id, utcnow
from app.database.base import Base, TimestampMixin, UTCDateTime


class Strategy(Base, TimestampMixin):
    __tablename__ = "strategies"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    active_version_id: Mapped[str | None] = mapped_column(String(16))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
                      UniqueConstraint("strategy_id", "file_hash", name="uq_strategy_hash"))
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(String(16), ForeignKey("strategies.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(32))
    file_hash: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str | None] = mapped_column(String(128))
    source_code: Mapped[str] = mapped_column(Text)          # authoritative copy (FS is only a cache)
    stored_path: Mapped[str | None] = mapped_column(String(512))
    uploaded_by: Mapped[int | None] = mapped_column(BigInteger)
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="VALIDATED")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)     # effective default parameters
    dependencies: Mapped[list[Any]] = mapped_column(JSON, default=list)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
