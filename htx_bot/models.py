from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)


@dataclass(frozen=True)
class StrategyConfig:
    capital: float = 1800.0
    max_open_positions: int = 2
    min_position_distance_usdt: float = 70.0
    fee_rate: float = 0.002
    slippage_rate: float = 0.0002
    target_price_delta: float = 10.0
    lookback: int = 90
    min_range_pct: float = 0.008
    max_slope_pct: float = 0.004
    entry_zone: float = 0.30
    exit_zone: float = 0.65
    min_touches: int = 2
    touch_tolerance: float = 0.12
    min_net_profit_pct: float = 0.0035
    cooldown_candles: int = 0


@dataclass
class Position:
    entry_time: int
    entry_price: float
    quantity: float
    buy_fee: float
    target_price: float


@dataclass(frozen=True)
class Trade:
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    quantity: float
    buy_fee: float
    sell_fee: float
    net_profit: float

    @property
    def holding_seconds(self) -> int:
        return self.exit_time - self.entry_time


@dataclass(frozen=True)
class BacktestResult:
    trades: list[Trade]
    position: Position | None
    realized_profit: float
    unrealized_profit: float
    total_profit: float
    max_drawdown: float
    exposure_seconds: int
    elapsed_seconds: int
    last_price: float
    position_age_seconds: int
