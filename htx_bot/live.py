from __future__ import annotations

import time
from datetime import datetime, timezone

from .config import LiveConfig
from .data import fetch_htx_candles
from .models import Candle
from .storage import Storage, StoredPosition
from .strategy import detect_range
from .telegram import TelegramClient


def _period_seconds(period: str) -> int:
    periods = {"1min": 60, "5min": 300, "15min": 900, "30min": 1800, "60min": 3600}
    if period not in periods:
        raise ValueError(f"Live-режим пока не поддерживает период {period}")
    return periods[period]


def _time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _duration(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}д {hours}ч {minutes}м"
    return f"{hours}ч {minutes}м"


class LiveSimulator:
    def __init__(self, config: LiveConfig, storage: Storage, telegram: TelegramClient):
        self.config = config
        self.storage = storage
        self.telegram = telegram
        self.period_seconds = _period_seconds(config.period)

    def _send(self, text: str, kind: str = "telegram") -> None:
        self.telegram.send(text)
        self.storage.event(int(time.time()), kind, text)

    def start_message(self) -> str:
        paused = self.storage.get("paused", False)
        return (
            "🧪 HTX СИМУЛЯТОР ЗАПУЩЕН\n\n"
            f"Пара: {self.config.symbol.upper()}\n"
            f"Капитал: {self.config.strategy.capital:.2f} USDT\n"
            f"Таймфрейм: {self.config.period}\n"
            f"Комиссия: {self.config.strategy.fee_rate * 100:.3f}% за операцию\n"
            f"Статус: {'ПАУЗА' if paused else 'АНАЛИЗ'}\n\n"
            "Реальные ордера и HTX API-ключи не используются.\n"
            "Команды: /status /position /stats /pause /resume /help"
        )

    def status_text(self) -> str:
        paused = self.storage.get("paused", False)
        last_price = float(self.storage.get("last_price", 0.0))
        last_candle = int(self.storage.get("last_candle_ts", 0))
        position = self.storage.position()
        state = "ПАУЗА" if paused else ("УДЕРЖАНИЕ" if position else "АНАЛИЗ")
        return (
            "📊 СТАТУС\n\n"
            f"Режим: SIMULATION\n"
            f"Состояние: {state}\n"
            f"Пара: {self.config.symbol.upper()}\n"
            f"Последняя цена: {last_price:.4f} USDT\n"
            f"Последняя закрытая свеча: {_time(last_candle) if last_candle else 'прогрев'}\n"
            f"Позиция: {'открыта' if position else 'нет'}"
        )

    def position_text(self, now: int | None = None) -> str:
        position = self.storage.position()
        if position is None:
            return "Открытой виртуальной позиции нет."
        now = now or int(time.time())
        price = float(self.storage.get("last_price", position.entry_price))
        value = position.quantity * price * (1 - self.config.strategy.fee_rate)
        pnl = value - self.config.strategy.capital
        return (
            "📌 ВИРТУАЛЬНАЯ ПОЗИЦИЯ\n\n"
            f"Покупка: {position.entry_price:.4f} USDT\n"
            f"Количество: {position.quantity:.6f} SOL\n"
            f"Текущая цена: {price:.4f} USDT\n"
            f"Продажа: {position.target_price:.4f} USDT\n"
            f"Текущий P&L: {pnl:+.2f} USDT\n"
            f"Удержание: {_duration(now - position.entry_time)}"
        )

    def stats_text(self) -> str:
        stats = self.storage.stats()
        return (
            "📈 СТАТИСТИКА СИМУЛЯТОРА\n\n"
            f"Завершённых сделок: {stats['count']}\n"
            f"Прибыльных: {stats['wins']}\n"
            f"Реализованный P&L: {stats['profit']:.2f} USDT\n"
            f"Средняя сделка: {stats['average']:.2f} USDT\n"
            f"Максимальная открытая просадка: {abs(float(self.storage.get('max_drawdown', 0.0))):.2f} USDT"
        )

    def handle_commands(self) -> None:
        offset = self.storage.get("telegram_offset")
        updates = self.telegram.updates(offset)
        for update in updates:
            update_id = int(update["update_id"])
            self.storage.set("telegram_offset", update_id + 1)
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            if int(chat.get("id", 0)) != self.config.telegram_chat_id:
                continue
            text = str(message.get("text", "")).strip()
            command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
            if command in {"/start", "/help"}:
                response = (
                    "Команды симулятора:\n"
                    "/status — состояние и цена\n"
                    "/position — открытая позиция\n"
                    "/stats — результаты\n"
                    "/pause — запрет новых покупок\n"
                    "/resume — продолжить поиск входа\n\n"
                    "Пауза не отменяет виртуальную продажу уже открытой позиции."
                )
            elif command == "/status":
                response = self.status_text()
            elif command == "/position":
                response = self.position_text()
            elif command == "/stats":
                response = self.stats_text()
            elif command == "/pause":
                self.storage.set("paused", True)
                response = "⏸ Новые виртуальные покупки приостановлены. Открытая позиция продолжает отслеживаться."
            elif command == "/resume":
                self.storage.set("paused", False)
                response = "▶️ Анализ и поиск новых виртуальных покупок продолжены."
            else:
                response = "Неизвестная команда. Используйте /help."
            self.telegram.send(response)

    def _sell(self, position: StoredPosition, timestamp: int) -> None:
        proceeds = position.quantity * position.target_price
        sell_fee = proceeds * self.config.strategy.fee_rate
        net = proceeds - sell_fee - self.config.strategy.capital
        self.storage.close_position(timestamp, position.target_price, sell_fee, net)
        cooldown_until = timestamp + self.config.strategy.cooldown_candles * self.period_seconds
        self.storage.set("cooldown_until", cooldown_until)
        stats = self.storage.stats()
        self._send(
            "✅ ВИРТУАЛЬНАЯ ПРОДАЖА\n\n"
            f"Время: {_time(timestamp)}\n"
            f"Покупка: {position.entry_price:.4f} USDT\n"
            f"Продажа: {position.target_price:.4f} USDT\n"
            f"Количество: {position.quantity:.6f} SOL\n"
            f"Комиссия продажи: {sell_fee:.2f} USDT\n"
            f"Чистая прибыль: {net:+.2f} USDT\n"
            f"Накопленная прибыль: {stats['profit']:.2f} USDT\n"
            f"Удержание: {_duration(timestamp - position.entry_time)}",
            "sell",
        )

    def _check_drawdown(self, position: StoredPosition, price: float, timestamp: int) -> None:
        drawdown_pct = price / position.entry_price - 1
        pnl = position.quantity * price * (1 - self.config.strategy.fee_rate) - self.config.strategy.capital
        max_drawdown = min(float(self.storage.get("max_drawdown", 0.0)), pnl)
        self.storage.set("max_drawdown", max_drawdown)
        crossed = [level for level in (-0.03, -0.05, -0.10, -0.20) if drawdown_pct <= level < position.last_alert_level]
        if not crossed:
            return
        level = min(crossed)
        self.storage.set_alert_level(level)
        self._send(
            "⚠️ ВИРТУАЛЬНАЯ ПОЗИЦИЯ В ПРОСАДКЕ\n\n"
            f"Покупка: {position.entry_price:.4f} USDT\n"
            f"Текущая цена: {price:.4f} USDT\n"
            f"Просадка цены: {drawdown_pct * 100:.2f}%\n"
            f"Текущий P&L: {pnl:+.2f} USDT\n"
            f"Цель продажи не изменена: {position.target_price:.4f} USDT\n"
            f"Удержание: {_duration(timestamp - position.entry_time)}",
            "drawdown",
        )

    def process_market(self, candles: list[Candle], now: int | None = None) -> str:
        if not candles:
            raise ValueError("HTX не вернул свечи")
        now = now or int(time.time())
        candles = sorted(candles, key=lambda candle: candle.timestamp)
        cutoff = now // self.period_seconds * self.period_seconds
        closed = [candle for candle in candles if candle.timestamp < cutoff]
        if not closed:
            raise ValueError("Нет закрытых свечей")
        self.storage.set("last_price", candles[-1].close)

        last_processed = self.storage.get("last_candle_ts")
        if last_processed is None:
            self.storage.set("last_candle_ts", closed[-1].timestamp)
            return "warmup"

        new_candles = [candle for candle in closed if candle.timestamp > int(last_processed)]
        if not new_candles:
            position = self.storage.position()
            if position:
                self._check_drawdown(position, candles[-1].close, now)
            return "idle"

        if int(last_processed) + self.period_seconds < closed[0].timestamp:
            gap_key = f"{last_processed}:{closed[0].timestamp}"
            if self.storage.get("last_gap_notice") != gap_key:
                self._send(
                    "⚠️ ПРОПУСК В ИСТОРИИ\n\n"
                    "Компьютер был выключен дольше доступной истории HTX. "
                    "Момент виртуального исполнения внутри пропуска нельзя восстановить точно.\n"
                    f"Последняя сохранённая свеча: {_time(int(last_processed))}\n"
                    f"Первая доступная свеча: {_time(closed[0].timestamp)}",
                    "data_gap",
                )
                self.storage.set("last_gap_notice", gap_key)

        # Existing virtual sell remains active during downtime and pause.
        position = self.storage.position()
        for candle in new_candles:
            if position and candle.high >= position.target_price:
                self._sell(position, candle.timestamp)
                position = None
            self.storage.set("last_candle_ts", candle.timestamp)

        position = self.storage.position()
        if position:
            self._check_drawdown(position, candles[-1].close, now)
            return "holding"
        if self.storage.get("paused", False):
            return "paused"
        if new_candles[-1].timestamp < int(self.storage.get("cooldown_until", 0)):
            return "cooldown"

        signal_candle = new_candles[-1]
        signal_index = next(i for i, candle in enumerate(closed) if candle.timestamp == signal_candle.timestamp)
        if signal_index < self.config.strategy.lookback or signal_index < 1:
            return "warmup"
        window = closed[signal_index - self.config.strategy.lookback:signal_index]
        signal = detect_range(window, self.config.strategy)
        previous = closed[signal_index - 1]
        reversal = signal_candle.close > previous.close and signal_candle.low >= previous.low
        if not signal or signal_candle.close > signal.entry_ceiling or not reversal:
            return "analysis"

        active = next((c for c in candles if c.timestamp > signal_candle.timestamp), None)
        fill_reference = active.open if active else signal_candle.close
        entry_price = fill_reference * (1 + self.config.strategy.slippage_rate)
        buy_fee = self.config.strategy.capital * self.config.strategy.fee_rate
        quantity = (self.config.strategy.capital - buy_fee) / entry_price
        minimum_target = entry_price * (
            1 + self.config.strategy.fee_rate + self.config.strategy.min_net_profit_pct
        ) / (1 - self.config.strategy.fee_rate)
        target = max(signal.target, minimum_target)
        if target > signal.resistance:
            return "target_outside_range"

        position = StoredPosition(
            entry_time=active.timestamp if active else now,
            entry_price=entry_price,
            quantity=quantity,
            buy_fee=buy_fee,
            target_price=target,
            last_alert_level=0.0,
        )
        self.storage.open_position(position)
        expected_proceeds = quantity * target * (1 - self.config.strategy.fee_rate)
        self._send(
            "🟢 ВИРТУАЛЬНАЯ ПОКУПКА\n\n"
            f"Время: {_time(position.entry_time)}\n"
            f"Цена: {entry_price:.4f} USDT\n"
            f"Количество: {quantity:.6f} SOL\n"
            f"Рабочий капитал: {self.config.strategy.capital:.2f} USDT\n"
            f"Комиссия покупки: {buy_fee:.2f} USDT\n\n"
            f"Виртуальная продажа: {target:.4f} USDT\n"
            f"Ожидаемая чистая прибыль: {expected_proceeds - self.config.strategy.capital:.2f} USDT\n"
            f"Диапазон: {signal.support:.4f}–{signal.resistance:.4f}",
            "buy",
        )
        return "bought"

    def daily_report(self, now: int | None = None) -> None:
        now = now or int(time.time())
        date = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        if self.storage.get("daily_report_date") == date:
            return
        hour = datetime.fromtimestamp(now, tz=timezone.utc).hour
        if hour < 20:
            return
        self._send("🗓 ЕЖЕДНЕВНЫЙ ОТЧЁТ\n\n" + self.stats_text() + "\n\n" + self.position_text(now), "daily")
        self.storage.set("daily_report_date", date)

    def run(self) -> None:
        bot = self.telegram.check()
        self._send(self.start_message(), "start")
        print(f"Telegram: @{bot.get('username', 'unknown')}")
        print("Live simulation started. Stop with Ctrl+C.")
        try:
            while True:
                try:
                    self.handle_commands()
                    self.storage.set("last_command_error", "")
                except Exception as error:
                    message = f"{type(error).__name__}: {error}"
                    if self.storage.get("last_command_error") != message:
                        print(f"Telegram commands: {message}")
                        self.storage.set("last_command_error", message)
                try:
                    last_candle = int(self.storage.get("last_candle_ts", int(time.time())))
                    missing = max(0, int(time.time()) - last_candle) // self.period_seconds
                    fetch_size = min(
                        2000,
                        max(200, self.config.strategy.lookback + 20, missing + 5),
                    )
                    candles = fetch_htx_candles(self.config.symbol, self.config.period, fetch_size)
                    self.process_market(candles)
                    self.daily_report()
                    self.storage.set("last_market_error", "")
                except Exception as error:
                    message = f"{type(error).__name__}: {error}"
                    if self.storage.get("last_market_error") != message:
                        try:
                            self._send("🔴 ОШИБКА СИМУЛЯТОРА\n\n" + message, "error")
                        except Exception:
                            pass
                        self.storage.set("last_market_error", message)
                    print(f"Market loop: {message}")
                time.sleep(self.config.poll_seconds)
        except KeyboardInterrupt:
            try:
                self._send("⏹ HTX симулятор остановлен с компьютера.", "stop")
            finally:
                print("Simulator stopped.")
