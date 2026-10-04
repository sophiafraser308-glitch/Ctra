"""Normalised broker data classes (protocol-independent). Prices are real floats, volumes in lots unless noted."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PRICE_SCALE = 100000


@dataclass
class SymbolInfo:
    symbol_id: int
    name: str
    digits: int = 5
    pip_position: int = 4
    lot_size: int = 10_000_000          # protocol volume units per 1.0 lot (cents of units)
    min_volume: int = 100_000
    step_volume: int = 100_000
    max_volume: int = 10_000_000_000
    base_asset_id: int | None = None
    quote_asset_id: int | None = None
    detailed: bool = False

    @property
    def pip_size(self) -> float:
        return 10 ** (-self.pip_position)

    def lots_to_protocol(self, lots: float) -> int:
        raw = int(round(lots * self.lot_size))
        step = max(1, self.step_volume)
        raw = (raw // step) * step
        return max(0, min(raw, self.max_volume))

    def protocol_to_lots(self, volume: int) -> float:
        return volume / self.lot_size if self.lot_size else 0.0

    @property
    def min_lots(self) -> float:
        return self.protocol_to_lots(self.min_volume)

    @property
    def step_lots(self) -> float:
        return self.protocol_to_lots(self.step_volume)

    @property
    def max_lots(self) -> float:
        return self.protocol_to_lots(self.max_volume)

    def round_price(self, p: float) -> float:
        return round(p, self.digits)


@dataclass
class BrokerTrader:
    ctid: int
    balance: float
    money_digits: int = 2
    deposit_asset_id: int | None = None
    leverage: float | None = None
    broker_name: str | None = None
    trader_login: int | None = None
    access_rights: str | None = None


@dataclass
class BrokerPosition:
    position_id: int
    symbol_id: int
    side: str                        # BUY | SELL
    volume: int                      # protocol volume
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    swap: float = 0.0
    commission: float = 0.0
    used_margin: float = 0.0
    status: str = "OPEN"
    open_ts_ms: int | None = None
    update_ts_ms: int | None = None
    label: str | None = None
    comment: str | None = None


@dataclass
class BrokerOrder:
    order_id: int
    symbol_id: int
    side: str
    order_type: str
    status: str                      # ACCEPTED | FILLED | REJECTED | EXPIRED | CANCELLED
    volume: int
    executed_volume: int = 0
    limit_price: float | None = None
    stop_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    execution_price: float | None = None
    client_order_id: str | None = None
    position_id: int | None = None
    update_ts_ms: int | None = None
    comment: str | None = None


@dataclass
class BrokerDeal:
    deal_id: int
    order_id: int | None
    position_id: int
    symbol_id: int
    side: str
    volume: int
    filled_volume: int
    price: float | None
    status: str
    commission: float = 0.0
    exec_ts_ms: int | None = None
    is_close: bool = False
    gross_profit: float = 0.0
    swap: float = 0.0
    close_commission: float = 0.0
    entry_price: float | None = None
    closed_volume: int = 0
    balance: float | None = None


@dataclass
class ExecutionEventData:
    ctid: int
    execution_type: str              # ORDER_ACCEPTED | ORDER_FILLED | ORDER_PARTIAL_FILL | ORDER_CANCELLED | ORDER_REJECTED | ...
    order: BrokerOrder | None = None
    position: BrokerPosition | None = None
    deal: BrokerDeal | None = None
    error_code: str | None = None
    is_server_event: bool = False
    received_at: datetime | None = None


@dataclass
class TickEvent:
    ctid: int
    symbol_id: int
    bid: float | None
    ask: float | None
    ts_ms: int


@dataclass
class BarData:
    symbol_id: int
    timeframe: str
    ts_ms: int                       # bar open time (UTC ms)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class AccountSession:
    account_id: str
    ctid: int
    environment: str
    desired: bool = False            # should be connected
    authed: bool = False
    symbols_by_id: dict[int, SymbolInfo] = field(default_factory=dict)
    symbols_by_name: dict[str, SymbolInfo] = field(default_factory=dict)
    assets: dict[int, str] = field(default_factory=dict)
    trader: BrokerTrader | None = None
    subscribed: set[int] = field(default_factory=set)
    last_event_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)
