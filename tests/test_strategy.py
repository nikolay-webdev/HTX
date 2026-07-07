from __future__ import annotations

import unittest

from htx_bot.models import Candle, StrategyConfig
from htx_bot.strategy import detect_range, run_backtest


def candle(index: int, close: float, low: float | None = None, high: float | None = None) -> Candle:
    return Candle(
        timestamp=1_700_000_000 + index * 60,
        open=close,
        high=high if high is not None else close + 0.2,
        low=low if low is not None else close - 0.2,
        close=close,
        volume=1000,
    )


class StrategyTests(unittest.TestCase):
    def test_detects_horizontal_range(self) -> None:
        candles = [candle(i, 81 + (i % 10) * 0.4, 80 if i % 10 == 0 else None, 85 if i % 10 == 9 else None) for i in range(120)]
        config = StrategyConfig(lookback=120, min_range_pct=0.01, max_slope_pct=0.02)
        self.assertIsNotNone(detect_range(candles, config))

    def test_rejects_strong_trend(self) -> None:
        candles = [candle(i, 80 + i * 0.1) for i in range(120)]
        config = StrategyConfig(lookback=120, min_range_pct=0.01, max_slope_pct=0.01)
        self.assertIsNone(detect_range(candles, config))

    def test_open_position_is_marked_to_market(self) -> None:
        closes = [80, 81, 82, 83, 84, 85, 84, 83, 82, 81] * 8
        candles = [candle(i, close, low=close - 0.1, high=close + 0.1) for i, close in enumerate(closes)]
        candles.extend([candle(80, 80.0), candle(81, 80.5, low=80.2)])
        candles.extend([candle(i, 75.0, low=74.8, high=75.2) for i in range(82, 90)])
        config = StrategyConfig(
            lookback=40,
            target_price_delta=1.0,
            min_range_pct=0.01,
            max_slope_pct=0.03,
            min_touches=1,
            entry_zone=0.5,
        )
        result = run_backtest(candles, config)
        self.assertIsNotNone(result.position)
        self.assertLess(result.unrealized_profit, 0)
        self.assertLess(result.total_profit, result.realized_profit)
        self.assertGreater(result.position_age_seconds, 0)

    def test_rebuys_on_next_candle_after_fixed_ten_dollar_sale(self) -> None:
        candles = [
            candle(0, 100, low=99, high=111),
            candle(1, 110, low=109, high=121),
            candle(2, 120, low=119, high=125),
        ]
        config = StrategyConfig(
            capital=1800,
            fee_rate=0,
            slippage_rate=0,
            target_price_delta=10,
            cooldown_candles=0,
        )

        result = run_backtest(candles, config)

        self.assertEqual(len(result.trades), 2)
        self.assertIsNotNone(result.position)
        assert result.position is not None
        self.assertEqual(result.position.entry_price, 120)
        self.assertEqual(result.position.target_price, 130)


if __name__ == "__main__":
    unittest.main()
