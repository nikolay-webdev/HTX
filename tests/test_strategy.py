from __future__ import annotations

import unittest

from htx_bot.models import Candle, StrategyConfig
from htx_bot.strategy import (
    detect_entry_setup,
    detect_range,
    minimum_profitable_target,
    run_backtest,
)


def candle(
    index: int,
    close: float,
    low: float | None = None,
    high: float | None = None,
    open_: float | None = None,
) -> Candle:
    return Candle(
        timestamp=1_700_000_000 + index * 60,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close + 0.2,
        low=low if low is not None else close - 0.2,
        close=close,
        volume=1000,
    )


class StrategyTests(unittest.TestCase):
    def range_window(self, *, green_signal: bool = True) -> list[Candle]:
        values = [100.0, 104.0, 108.0, 104.0]
        result = [candle(i, values[i % len(values)]) for i in range(119)]
        result.append(candle(
            119,
            100.5,
            low=99.8,
            high=100.7,
            open_=100.0 if green_signal else 101.0,
        ))
        return result

    def test_detects_horizontal_range(self) -> None:
        candles = [candle(i, 81 + (i % 10) * 0.4, 80 if i % 10 == 0 else None, 85 if i % 10 == 9 else None) for i in range(120)]
        config = StrategyConfig(lookback=120, min_range_pct=0.01, max_slope_pct=0.02)
        self.assertIsNotNone(detect_range(candles, config))

    def test_rejects_strong_trend(self) -> None:
        candles = [candle(i, 80 + i * 0.1) for i in range(120)]
        config = StrategyConfig(lookback=120, min_range_pct=0.01, max_slope_pct=0.01)
        self.assertIsNone(detect_range(candles, config))

    def test_entry_requires_green_confirmation_near_support(self) -> None:
        config = StrategyConfig(
            lookback=120,
            target_price_delta=1.0,
            min_range_pct=0.01,
            max_slope_pct=0.10,
            min_touches=1,
        )
        self.assertIsNotNone(detect_entry_setup(self.range_window(), 100.6, config))
        self.assertIsNone(detect_entry_setup(
            self.range_window(green_signal=False), 100.6, config
        ))

    def test_target_covers_fees_and_minimum_profit(self) -> None:
        config = StrategyConfig(target_price_delta=1.0, min_net_profit_pct=0.0035)
        target = minimum_profitable_target(1736.31, config)
        self.assertGreater(target, 1736.31 + 10)

    def test_open_position_is_marked_to_market(self) -> None:
        candles = self.range_window()
        candles.append(candle(120, 95.0, low=94.8, high=100.8, open_=100.6))
        candles.append(candle(121, 94.0, low=93.8, high=95.0))
        config = StrategyConfig(
            lookback=120,
            target_price_delta=1.0,
            min_range_pct=0.01,
            max_slope_pct=0.10,
            min_touches=1,
        )
        result = run_backtest(candles, config)
        self.assertIsNotNone(result.position)
        self.assertLess(result.unrealized_profit, 0)
        self.assertLess(result.total_profit, result.realized_profit)
        self.assertGreater(result.position_age_seconds, 0)

    def test_backtest_enters_only_after_confirmed_range_signal(self) -> None:
        candles = self.range_window()
        candles.append(candle(120, 102.0, low=100.5, high=102.0, open_=100.6))
        config = StrategyConfig(
            capital=1800,
            fee_rate=0,
            slippage_rate=0,
            target_price_delta=1,
            lookback=120,
            min_range_pct=0.01,
            max_slope_pct=0.10,
            min_touches=1,
        )

        result = run_backtest(candles, config)

        self.assertEqual(len(result.trades), 1)
        self.assertIsNone(result.position)
        self.assertEqual(result.trades[0].entry_price, 100.6)
        self.assertEqual(result.trades[0].exit_price, 101.6)


if __name__ == "__main__":
    unittest.main()
