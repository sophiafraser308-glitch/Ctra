from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import new_id, utcnow
from app.database.base import Base, UTCDateTime


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    strategy_id: Mapped[str] = mapped_column(String(16))
    strategy_version_id: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[str] = mapped_column(String(16))
    symbols: Mapped[list[Any]] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(4))
    date_from: Mapped[datetime] = mapped_column(UTCDateTime)
    date_to: Mapped[datetime] = mapped_column(UTCDateTime)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="RUNNING", index=True)    # RUNNING | DONE | FAILED | CANCELLED
    bars_processed: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
