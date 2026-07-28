from __future__ import annotations

from dataclasses import dataclass
from statistics import median
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


@dataclass(frozen=True)
class EntrySetup:
    support: float
    resistance: float
    entry_ceiling: float
    entry_price: float
    target_price: float
    kind: str = "range"


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


def minimum_profitable_target(entry_price: float, config: StrategyConfig) -> float:
    """Return the exit price needed to cover fees and the configured net return."""
    buy_fee = config.capital * config.fee_rate
    quantity = (config.capital - buy_fee) / entry_price
    required_proceeds = config.capital * (1 + config.min_net_profit_pct)
    return required_proceeds / (quantity * (1 - config.fee_rate))


def detect_range_entry_setup(
    window: list[Candle],
    next_open: float,
    config: StrategyConfig,
) -> EntrySetup | None:
    """Find a confirmed lower-range entry without using the next candle's future data."""
    if len(window) < config.lookback:
        return None
    analysis_window = window[-config.lookback:]
    range_signal = detect_range(analysis_window, config)
    if range_signal is None:
        return None

    signal = analysis_window[-1]
    if signal.close <= signal.open:
        return None
    if signal.low > range_signal.entry_ceiling or signal.close > range_signal.entry_ceiling:
        return None

    entry_price = next_open * (1 + config.slippage_rate)
    target_price = max(
        entry_price + config.target_price_delta,
        minimum_profitable_target(entry_price, config),
    )
    if target_price > range_signal.resistance:
        return None

    return EntrySetup(
        support=range_signal.support,
        resistance=range_signal.resistance,
        entry_ceiling=range_signal.entry_ceiling,
        entry_price=entry_price,
        target_price=target_price,
    )


def detect_reversal_entry_setup(
    window: list[Candle],
    next_open: float,
    config: StrategyConfig,
) -> EntrySetup | None:
    """Detect a high-volume flush and rebound, then hold until a profitable target."""
    if not config.reversal_enabled or len(window) < config.reversal_lookback:
        return None

    analysis = window[-config.reversal_lookback:]
    confirmation_start = len(analysis) - config.reversal_confirmation_candles
    recent = analysis[confirmation_start:]
    breakdown_offset = min(range(len(recent)), key=lambda index: recent[index].low)
    breakdown_index = confirmation_start + breakdown_offset
    breakdown = analysis[breakdown_index]

    if breakdown_index < config.reversal_volume_lookback:
        return None
    history_before_breakdown = analysis[:breakdown_index]
    if not history_before_breakdown:
        return None

    reference_high = max(candle.high for candle in history_before_breakdown)
    drop_pct = 1 - breakdown.low / reference_high
    if drop_pct < config.reversal_drop_pct:
        return None

    volume_window = analysis[
        breakdown_index - config.reversal_volume_lookback:breakdown_index
    ]
    baseline_volume = median(candle.volume for candle in volume_window)
    if baseline_volume <= 0:
        return None
    if breakdown.volume < baseline_volume * config.reversal_volume_multiplier:
        return None

    signal = analysis[-1]
    if signal.close <= signal.open:
        return None
    rebound_pct = signal.close / breakdown.low - 1
    if not config.reversal_rebound_pct <= rebound_pct <= config.reversal_max_rebound_pct:
        return None
    if signal.close <= breakdown.close:
        return None

    entry_price = next_open * (1 + config.slippage_rate)
    target_price = max(
        entry_price + config.target_price_delta,
        minimum_profitable_target(entry_price, config),
    )
    return EntrySetup(
        support=breakdown.low,
        resistance=reference_high,
        entry_ceiling=signal.close,
        entry_price=entry_price,
        target_price=target_price,
        kind="reversal_hold",
    )


def detect_entry_setup(
    window: list[Candle],
    next_open: float,
    config: StrategyConfig,
) -> EntrySetup | None:
    range_setup = detect_range_entry_setup(window, next_open, config)
    if range_setup is not None:
        return range_setup
    return detect_reversal_entry_setup(window, next_open, config)


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
            history_size = max(
                config.lookback,
                config.reversal_lookback if config.reversal_enabled else 0,
            )
            window = candles[max(0, index - history_size):index]
            setup = detect_entry_setup(window, candle.open, config)
            if setup is not None:
                entry_price = setup.entry_price
                buy_fee = config.capital * config.fee_rate
                quantity = (config.capital - buy_fee) / entry_price
                target = setup.target_price
                position = Position(
                    candle.timestamp,
                    entry_price,
                    quantity,
                    buy_fee,
                    target,
                    setup.kind,
                )

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
                    entry_kind=position.entry_kind,
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
