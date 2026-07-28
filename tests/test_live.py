from __future__ import annotations

import tempfile
import unittest
import sqlite3
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
            max_open_positions=2,
            min_position_distance_usdt=5,
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

    def test_second_lower_position_can_open_while_first_is_holding(self) -> None:
        initial = [
            make_candle(0, 82),
            make_candle(1, 80, high=100),
            make_candle(2, 80),
        ]
        self.simulator.process_market(initial, initial[-1].timestamp + 60)

        signal = make_candle(3, 81, low=79.8, open_=79)
        active = make_candle(4, 81)
        self.assertEqual(
            self.simulator.process_market(initial + [signal, active], active.timestamp + 30),
            "bought",
        )

        lower_range = [
            make_candle(5, 75, high=88, low=74),
            make_candle(6, 75, high=88, low=74),
            make_candle(7, 75, high=88, low=74),
        ]
        lower_signal = make_candle(8, 76, high=78, low=73.8, open_=74)
        lower_active = make_candle(9, 76)

        result = self.simulator.process_market(
            initial + [signal, active] + lower_range + [lower_signal, lower_active],
            lower_active.timestamp + 30,
        )

        self.assertEqual(result, "bought")
        self.assertEqual(len(self.storage.positions()), 2)
        self.assertIn("Позиция: #2", self.telegram.sent[-1])
        self.assertIn("Открытые позиции после покупки: 2 / 2", self.telegram.sent[-1])

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

    def test_reversal_hold_position_is_not_sold_during_drawdown(self) -> None:
        initial = [make_candle(0, 82), make_candle(1, 83), make_candle(2, 82)]
        self.simulator.process_market(initial, initial[-1].timestamp + 60)
        current = make_candle(3, 70, high=79.0, low=69.0)
        self.storage.open_position(
            StoredPosition(
                current.timestamp,
                80.0,
                1.0,
                3.6,
                90.0,
                0.0,
                entry_kind="reversal_hold",
            )
        )

        result = self.simulator.process_market(initial + [current], current.timestamp + 30)

        self.assertEqual(result, "holding")
        self.assertIsNotNone(self.storage.position())
        self.assertEqual(self.storage.stats()["count"], 0)

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
        expected = StoredPosition(100, 80.0, 6.2, 1.0, 82.0, 0.0, 80.0, 80.0)
        self.storage.open_position(expected)
        self.storage.close()
        self.storage = Storage(self.config.database_path)
        self.simulator.storage = self.storage
        self.assertEqual(self.storage.position(), expected)

    def test_old_single_position_database_migrates_to_multiple_positions(self) -> None:
        self.storage.close()
        database_path = self.config.database_path
        database_path.unlink(missing_ok=True)
        connection = sqlite3.connect(database_path)
        connection.executescript(
            """
            CREATE TABLE state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE position (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                entry_time INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                buy_fee REAL NOT NULL,
                target_price REAL NOT NULL,
                last_alert_level REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_time INTEGER NOT NULL,
                exit_time INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                buy_fee REAL NOT NULL,
                sell_fee REAL NOT NULL,
                net_profit REAL NOT NULL
            );
            INSERT INTO position VALUES(1, 100, 1800, 1, 3.6, 1810, 0);
            """
        )
        connection.commit()
        connection.close()

        self.storage = Storage(database_path)
        first = self.storage.position()
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.entry_price, 1800)

        second = self.storage.open_position(
            StoredPosition(200, 1700.0, 1.05, 3.6, 1710.0, 0.0)
        )

        self.assertEqual(second.id, 2)
        self.assertEqual(len(self.storage.positions()), 2)

    def test_stats_track_price_potential_after_entry(self) -> None:
        initial = [
            make_candle(0, 82),
            make_candle(1, 80, high=100),
            make_candle(2, 80),
        ]
        self.simulator.process_market(initial, initial[-1].timestamp + 60)

        signal = make_candle(3, 81, low=79.8, open_=79)
        active = make_candle(4, 81)
        self.simulator.process_market(initial + [signal, active], active.timestamp + 30)

        dip = make_candle(5, 82, high=84, low=77)
        rally = make_candle(6, 93, high=93)
        current = make_candle(7, 93)
        self.simulator.process_market(
            initial + [signal, active, dip, rally, current], current.timestamp + 30
        )

        stats = self.storage.stats()
        self.assertEqual(stats["count"], 1)
        self.assertGreaterEqual(stats["reached_10"], 1)
        self.assertGreaterEqual(stats["best_max_move"], 10)
        self.assertLessEqual(stats["worst_min_move"], -4)
        self.assertIn("Потенциал после входа", self.simulator.stats_text())

    def test_stats_separate_archived_reversal_trades(self) -> None:
        self.storage.connection.execute(
            """INSERT INTO trades(
                   entry_time, exit_time, entry_price, exit_price, quantity,
                   buy_fee, sell_fee, net_profit, max_price, min_price,
                   entry_kind, exit_reason
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (100, 200, 100.0, 90.0, 1.0, 3.6, 3.6, -17.2, 101.0, 90.0, "reversal", "stop"),
        )
        self.storage.connection.execute(
            """INSERT INTO trades(
                   entry_time, exit_time, entry_price, exit_price, quantity,
                   buy_fee, sell_fee, net_profit, max_price, min_price,
                   entry_kind, exit_reason
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (300, 400, 100.0, 110.0, 1.0, 3.6, 3.6, 2.8, 112.0, 99.0, "range", "target"),
        )
        self.storage.connection.execute(
            """INSERT INTO trades(
                   entry_time, exit_time, entry_price, exit_price, quantity,
                   buy_fee, sell_fee, net_profit, max_price, min_price,
                   entry_kind, exit_reason
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (500, 600, 100.0, 110.0, 1.0, 3.6, 3.6, 6.3, 111.0, 88.0, "reversal_hold", "target"),
        )
        self.storage.connection.execute(
            """INSERT INTO trades(
                   entry_time, exit_time, entry_price, exit_price, quantity,
                   buy_fee, sell_fee, net_profit, max_price, min_price,
                   entry_kind, exit_reason
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (700, 800, 100.0, 112.0, 1.0, 3.6, 3.6, 7.0, 113.0, 96.0, "reversal", "target"),
        )
        self.storage.connection.commit()

        stats = self.storage.stats()
        self.assertEqual(stats["count"], 3)
        self.assertAlmostEqual(stats["profit"], 16.1)
        self.assertEqual(stats["range_count"], 1)
        self.assertEqual(stats["reversal_hold_count"], 1)
        self.assertEqual(stats["reversal_target_count"], 2)
        self.assertEqual(stats["archived_count"], 1)
        self.assertEqual(stats["archived_stops"], 1)
        self.assertAlmostEqual(stats["archived_profit"], -17.2)

        report = self.simulator.stats_text()
        self.assertIn("СТАТИСТИКА ТЕКУЩЕЙ СТРАТЕГИИ", report)
        self.assertIn("АРХИВ СТАРЫХ СТОПОВ", report)
        self.assertIn("Зафиксированный тогда P&L: -17.20 USDT", report)


if __name__ == "__main__":
    unittest.main()
