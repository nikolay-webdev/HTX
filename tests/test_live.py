from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from htx_bot.config import LiveConfig
from htx_bot.live import LiveSimulator
from htx_bot.models import Candle, StrategyConfig
from htx_bot.storage import Storage, StoredPosition


class FakeTelegram:
    def __init__(self, updates=None):
        self.sent: list[str] = []
        self._updates = updates or []

    def send(self, text: str) -> None:
        self.sent.append(text)

    def updates(self, offset=None):
        return self._updates


def make_candle(
    index: int,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
) -> Candle:
    timestamp = 1_700_000_040 + index * 60
    return Candle(
        timestamp=timestamp,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close + 0.1,
        low=low if low is not None else close - 0.1,
        close=close,
        volume=100,
    )


class LiveSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        strategy = StrategyConfig(
            capital=1800,
            fee_rate=0.002,
            slippage_rate=0,
            target_price_delta=10,
            lookback=3,
            min_range_pct=0.01,
            max_slope_pct=1,
            min_touches=1,
        )
        self.config = LiveConfig(
            telegram_token="test",
            telegram_chat_id=123,
            symbol="ethusdt",
            period="1min",
            poll_seconds=10,
            database_path=Path(self.temp.name) / "state.db",
            strategy=strategy,
        )
        self.storage = Storage(self.config.database_path)
        self.telegram = FakeTelegram()
        self.simulator = LiveSimulator(self.config, self.storage, self.telegram)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def test_first_market_load_only_warms_up(self) -> None:
        candles = [make_candle(0, 82), make_candle(1, 83), make_candle(2, 82)]
        now = candles[-1].timestamp + 60
        self.assertEqual(self.simulator.process_market(candles, now), "warmup")
        self.assertIsNone(self.storage.position())
        self.assertEqual(self.telegram.sent, [])

    def test_virtual_buy_and_sell_are_persisted_and_notified(self) -> None:
        initial = [
            make_candle(0, 82),
            make_candle(1, 80, high=100),
            make_candle(2, 80),
        ]
        self.simulator.process_market(initial, initial[-1].timestamp + 60)

        signal = make_candle(3, 81, low=79.8, open_=79)
        active = make_candle(4, 81)
        result = self.simulator.process_market(initial + [signal, active], active.timestamp + 30)
        self.assertEqual(result, "bought")
        position = self.storage.position()
        self.assertIsNotNone(position)
        assert position is not None
        self.assertEqual(position.target_price, position.entry_price + 10)
        self.assertIn("ВИРТУАЛЬНАЯ ПОКУПКА", self.telegram.sent[-1])
        self.assertIn("ETH", self.telegram.sent[-1])

        sell = make_candle(5, 93, high=93)
        current = make_candle(6, 93)
        result = self.simulator.process_market(
            initial + [signal, active, sell, current], current.timestamp + 30
        )
        self.assertEqual(result, "sold")
        self.assertIsNone(self.storage.position())
        self.assertEqual(self.storage.stats()["count"], 1)
        self.assertIn("ВИРТУАЛЬНАЯ ПРОДАЖА", self.telegram.sent[-1])

    def test_live_waits_when_last_candle_does_not_confirm_rebound(self) -> None:
        initial = [
            make_candle(0, 82),
            make_candle(1, 80, high=100),
            make_candle(2, 80),
        ]
        self.simulator.process_market(initial, initial[-1].timestamp + 60)
        red_signal = make_candle(3, 79, low=78.5, open_=81)
        active = make_candle(4, 79)

        result = self.simulator.process_market(
            initial + [red_signal, active], active.timestamp + 30
        )

        self.assertEqual(result, "analyzing")
        self.assertIsNone(self.storage.position())
        self.assertEqual(self.telegram.sent, [])

    def test_current_minute_high_triggers_fixed_target_without_waiting_for_close(self) -> None:
        initial = [make_candle(0, 82), make_candle(1, 83), make_candle(2, 82)]
        self.simulator.process_market(initial, initial[-1].timestamp + 60)
        current = make_candle(3, 89, high=90.1)
        self.storage.open_position(
            StoredPosition(current.timestamp, 80.0, 1.0, 3.6, 90.0, 0.0)
        )

        result = self.simulator.process_market(initial + [current], current.timestamp + 30)

        self.assertEqual(result, "sold")
        self.assertIsNone(self.storage.position())
        self.assertIn("Разница цены: +10.00 USDT", self.telegram.sent[-1])

    def test_only_authorized_chat_can_pause(self) -> None:
        self.telegram._updates = [
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/pause"}},
            {"update_id": 2, "message": {"chat": {"id": 123}, "text": "/pause"}},
        ]
        self.simulator.handle_commands()
        self.assertTrue(self.storage.get("paused"))
        self.assertEqual(len(self.telegram.sent), 1)

    def test_status_includes_open_position_details(self) -> None:
        self.storage.set("last_price", 1738.72)
        self.storage.set("last_candle_ts", 1_700_000_000)
        self.storage.open_position(
            StoredPosition(1_700_000_000, 1805.581, 0.994915, 3.6, 1815.581, 0.0)
        )

        text = self.simulator.status_text()

        self.assertIn("Состояние: УДЕРЖАНИЕ", text)
        self.assertIn("Цена покупки: 1805.5810 USDT", text)
        self.assertIn("Текущая цена: 1738.7200 USDT", text)
        self.assertIn("Ожидаемая цена продажи: 1815.5810 USDT", text)
        self.assertIn("Текущий P&L:", text)

    def test_current_market_price_command(self) -> None:
        self.storage.set("last_price", 1738.72)
        self.storage.set("last_candle_ts", 1_700_000_000)
        self.telegram._updates = [{
            "update_id": 3,
            "message": {"chat": {"id": 123}, "text": "/current_market_price"},
        }]

        self.simulator.handle_commands()

        self.assertIn("ТЕКУЩАЯ РЫНОЧНАЯ ЦЕНА", self.telegram.sent[-1])
        self.assertIn("Цена: 1738.7200 USDT", self.telegram.sent[-1])

    def test_start_message_reports_restored_position_as_holding(self) -> None:
        self.storage.open_position(
            StoredPosition(1_700_000_000, 1805.581, 0.994915, 3.6, 1815.581, 0.0)
        )

        self.assertIn("Статус: УДЕРЖАНИЕ", self.simulator.start_message())

    def test_position_survives_database_reopen(self) -> None:
        expected = StoredPosition(100, 80.0, 6.2, 1.0, 82.0, 0.0)
        self.storage.open_position(expected)
        self.storage.close()
        self.storage = Storage(self.config.database_path)
        self.simulator.storage = self.storage
        self.assertEqual(self.storage.position(), expected)


if __name__ == "__main__":
    unittest.main()
