from __future__ import annotations

from dataclasses import dataclass
from .models import BacktestResult, Candle, Position, StrategyConfig, Trade


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Нельзя вычислить процентиль пустого списка")
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class RangeSignal:
    support: float
    resistance: float
    entry_ceiling: float
    target: float


def detect_range(window: list[Candle], config: StrategyConfig) -> RangeSignal | None:
    support = percentile([c.low for c in window], 0.15)
    resistance = percentile([c.high for c in window], 0.85)
    if support <= 0 or resistance <= support:
        return None
    width = resistance - support
    if width / support < config.min_range_pct:
        return None

    segment = max(2, len(window) // 4)
    first = sum(c.close for c in window[:segment]) / segment
    last = sum(c.close for c in window[-segment:]) / segment
    if abs(last / first - 1) > config.max_slope_pct:
        return None

    tolerance = width * config.touch_tolerance
    low_touches = sum(c.low <= support + tolerance for c in window)
    high_touches = sum(c.high >= resistance - tolerance for c in window)
    if low_touches < config.min_touches or high_touches < config.min_touches:
        return None

    return RangeSignal(
        support=support,
        resistance=resistance,
        entry_ceiling=support + width * config.entry_zone,
        target=support + width * config.exit_zone,
    )


def run_backtest(candles: list[Candle], config: StrategyConfig) -> BacktestResult:
    if len(candles) < 2:
        raise ValueError("Нужно не меньше двух свечей")
    candles = sorted(candles, key=lambda candle: candle.timestamp)

    trades: list[Trade] = []
    position: Position | None = None
    cooldown_until = -1
    realized = 0.0
    exposure_seconds = 0
    peak_equity = config.capital
    max_drawdown = 0.0

    previous_time = candles[0].timestamp
    for index, candle in enumerate(candles):
        interval = max(0, candle.timestamp - previous_time)
        previous_time = candle.timestamp

        if position is None and index >= cooldown_until:
            entry_price = candle.open * (1 + config.slippage_rate)
            buy_fee = config.capital * config.fee_rate
            quantity = (config.capital - buy_fee) / entry_price
            target = entry_price + config.target_price_delta
            position = Position(candle.timestamp, entry_price, quantity, buy_fee, target)

        if position is not None:
            exposure_seconds += interval
            if candle.high >= position.target_price:
                proceeds = position.quantity * position.target_price
                sell_fee = proceeds * config.fee_rate
                net = proceeds - sell_fee - config.capital
                trades.append(Trade(
                    entry_time=position.entry_time,
                    exit_time=candle.timestamp,
                    entry_price=position.entry_price,
                    exit_price=position.target_price,
                    quantity=position.quantity,
                    buy_fee=position.buy_fee,
                    sell_fee=sell_fee,
                    net_profit=net,
                ))
                realized += net
                position = None
                cooldown_until = index + config.cooldown_candles

        market_value = config.capital
        if position is not None:
            market_value = position.quantity * candle.close * (1 - config.fee_rate)
        equity = realized + market_value
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)

    last = candles[-1]
    unrealized = 0.0
    if position is not None:
        liquidation_value = position.quantity * last.close * (1 - config.fee_rate)
        unrealized = liquidation_value - config.capital

    elapsed = candles[-1].timestamp - candles[0].timestamp
    return BacktestResult(
        trades=trades,
        position=position,
        realized_profit=realized,
        unrealized_profit=unrealized,
        total_profit=realized + unrealized,
        max_drawdown=max_drawdown,
        exposure_seconds=exposure_seconds,
        elapsed_seconds=elapsed,
        last_price=last.close,
        position_age_seconds=(last.timestamp - position.entry_time) if position else 0,
    )
