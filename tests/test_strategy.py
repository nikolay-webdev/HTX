from __future__ import annotations

import unittest

from htx_bot.models import Candle, StrategyConfig
from htx_bot.strategy import (
    detect_entry_setup,
    detect_range,
    detect_reversal_entry_setup,
    minimum_profitable_target,
    run_backtest,
)


def candle(
    index: int,
    close: float,
    low: float | None = None,
    high: float | None = None,
    open_: float | None = None,
    volume: float = 1000,
) -> Candle:
    return Candle(
        timestamp=1_700_000_000 + index * 60,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close + 0.2,
        low=low if low is not None else close - 0.2,
        close=close,
        volume=volume,
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

    def reversal_window(self, *, breakdown_volume: float = 1000) -> list[Candle]:
        result = [candle(i, 100.0, volume=100) for i in range(45)]
        result.append(candle(
            45,
            99.25,
            low=99.2,
            high=100.0,
            open_=99.8,
            volume=breakdown_volume,
        ))
        result.extend(
            candle(i, 99.35, low=99.3, high=99.5, volume=100)
            for i in range(46, 59)
        )
        result.append(candle(
            59,
            99.65,
            low=99.4,
            high=99.7,
            open_=99.4,
            volume=200,
        ))
        return result

    def reversal_config(self) -> StrategyConfig:
        return StrategyConfig(
            capital=1800,
            fee_rate=0,
            slippage_rate=0,
            target_price_delta=1,
            reversal_enabled=True,
            reversal_lookback=60,
            reversal_confirmation_candles=15,
            reversal_drop_pct=0.004,
            reversal_rebound_pct=0.0025,
            reversal_max_rebound_pct=0.008,
            reversal_volume_lookback=20,
            reversal_volume_multiplier=2,
        )

    def test_detects_reversal_hold_without_stop(self) -> None:
        setup = detect_reversal_entry_setup(
            self.reversal_window(),
            99.7,
            self.reversal_config(),
        )

        self.assertIsNotNone(setup)
        assert setup is not None
        self.assertEqual(setup.kind, "reversal_hold")
        self.assertAlmostEqual(setup.entry_price, 99.7)
        self.assertAlmostEqual(setup.target_price, 100.7)

    def test_reversal_rejects_breakdown_without_volume_confirmation(self) -> None:
        setup = detect_reversal_entry_setup(
            self.reversal_window(breakdown_volume=100),
            99.7,
            self.reversal_config(),
        )

        self.assertIsNone(setup)

    def test_reversal_hold_survives_drop_and_closes_only_at_target(self) -> None:
        candles = self.reversal_window()
        candles.append(candle(60, 99.7, low=99.5, high=99.8, open_=99.7))
        candles.append(candle(61, 90.0, low=89.0, high=99.6, open_=99.5))
        held = run_backtest(candles, self.reversal_config())

        self.assertEqual(held.trades, [])
        self.assertIsNotNone(held.position)
        assert held.position is not None
        self.assertEqual(held.position.entry_kind, "reversal_hold")

        candles.append(candle(62, 101.0, low=90.0, high=101.0, open_=90.0))
        sold = run_backtest(candles, self.reversal_config())
        self.assertEqual(len(sold.trades), 1)
        self.assertEqual(sold.trades[0].entry_kind, "reversal_hold")
        self.assertGreater(sold.trades[0].net_profit, 0)

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
