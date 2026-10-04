from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SignalIn(BaseModel):
    """Validated signal as emitted by a strategy worker (untrusted input!)."""
    symbol: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_.#/-]+$")
    side: Literal["BUY", "SELL", "CLOSE"]
    order_type: Literal["MARKET", "LIMIT", "STOP"] = "MARKET"
    price: float | None = Field(default=None, gt=0)
    stop_loss_pips: float | None = Field(default=None, gt=0, le=100000)
    take_profit_pips: float | None = Field(default=None, gt=0, le=100000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    comment: str | None = Field(default=None, max_length=64)

    @field_validator("symbol")
    @classmethod
    def _norm(cls, v: str) -> str:
        return v.upper()


class RiskDecision(BaseModel):
    approved: bool
    reason: str
    lots: float = 0.0
    detail: dict[str, Any] = Field(default_factory=dict)


class OrderIntent(BaseModel):
    request_id: str
    correlation_id: str
    account_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "STOP"] = "MARKET"
    volume_lots: float = Field(gt=0)
    price: float | None = None
    stop_loss_pips: float | None = None
    take_profit_pips: float | None = None
    bot_id: str | None = None
    strategy_id: str | None = None
    strategy_version_id: str | None = None
    signal_id: str | None = None
    comment: str | None = None
    retry_of: str | None = None
