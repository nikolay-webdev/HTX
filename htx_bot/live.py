from __future__ import annotations

import time
from datetime import datetime, timezone

from .config import LiveConfig
from .data import fetch_htx_candles
from .models import Candle
from .storage import Storage, StoredPosition
from .strategy import detect_entry_setup
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


def _base_asset(symbol: str) -> str:
    value = symbol.upper()
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[:-len(quote)]
    return value


def _target_recommendation(stats: dict[str, float | int]) -> str:
    count = int(stats["count"])
    if count < 10:
        return "Данных мало: для выбора цели нужно хотя бы 10 закрытых сделок. Пока безопаснее тестировать +10/+12."

    levels = [
        (25, int(stats["reached_25"])),
        (20, int(stats["reached_20"])),
        (15, int(stats["reached_15"])),
        (10, int(stats["reached_10"])),
    ]
    for level, reached in levels:
        hit_rate = reached / count
        if hit_rate >= 0.6:
            return f"По статистике можно тестировать цель около +{level} USDT: цена доходила туда в {hit_rate * 100:.0f}% сделок."
    return "По статистике дальние цели пока слабые. Лучше оставаться на +10 и копить данные."


class LiveSimulator:
    def __init__(self, config: LiveConfig, storage: Storage, telegram: TelegramClient):
        self.config = config
        self.storage = storage
        self.telegram = telegram
        self.period_seconds = _period_seconds(config.period)
        self.base_asset = _base_asset(config.symbol)

    @property
    def total_capital(self) -> float:
        return self.config.strategy.capital * self.config.strategy.max_open_positions

    def _send(self, text: str, kind: str = "telegram") -> None:
        self.telegram.send(text)
        self.storage.event(int(time.time()), kind, text)

    def _state(self, paused: bool, positions: list[StoredPosition]) -> str:
        if paused:
            return "ПАУЗА"
        if len(positions) >= self.config.strategy.max_open_positions:
            return "УДЕРЖАНИЕ"
        if positions:
            return "УДЕРЖАНИЕ + АНАЛИЗ"
        return "АНАЛИЗ"

    def _position_block(self, position: StoredPosition, price: float, now: int, title: str | None = None) -> str:
        value = position.quantity * price * (1 - self.config.strategy.fee_rate)
        pnl = value - self.config.strategy.capital
        max_move = (position.max_price or position.entry_price) - position.entry_price
        min_move = (position.min_price or position.entry_price) - position.entry_price
        header = title or f"📌 ПОЗИЦИЯ #{position.id}"
        return (
            f"{header}\n"
            f"Цена покупки: {position.entry_price:.4f} USDT\n"
            f"Количество: {position.quantity:.6f} {self.base_asset}\n"
            f"Текущая цена: {price:.4f} USDT\n"
            f"Ожидаемая цена продажи: {position.target_price:.4f} USDT\n"
            f"Осталось до цели: {position.target_price - price:+.4f} USDT\n"
            f"Текущий P&L: {pnl:+.2f} USDT\n"
            f"Максимум после входа: {(position.max_price or position.entry_price):.4f} USDT ({max_move:+.2f})\n"
            f"Минимум после входа: {(position.min_price or position.entry_price):.4f} USDT ({min_move:+.2f})\n"
            f"Удержание: {_duration(now - position.entry_time)}"
        )

    def _can_open_new_position(self, entry_price: float, positions: list[StoredPosition]) -> tuple[bool, str]:
        if len(positions) >= self.config.strategy.max_open_positions:
            return False, "position_limit"
        if not positions:
            return True, "first_position"
        lowest_entry = min(position.entry_price for position in positions)
        required = lowest_entry - self.config.strategy.min_position_distance_usdt
        if entry_price > required:
            return False, "price_too_close_to_existing_position"
        return True, "lower_slot"

    def start_message(self) -> str:
        paused = self.storage.get("paused", False)
        positions = self.storage.positions()
        state = self._state(paused, positions)
        return (
            "🧪 HTX СИМУЛЯТОР ЗАПУЩЕН\n\n"
            f"Пара: {self.config.symbol.upper()}\n"
            f"Общий виртуальный бюджет: {self.total_capital:.2f} USDT\n"
            f"Капитал на одну позицию: {self.config.strategy.capital:.2f} USDT\n"
            f"Максимум открытых позиций: {self.config.strategy.max_open_positions}\n"
            f"Минимальная дистанция между входами: {self.config.strategy.min_position_distance_usdt:.2f} USDT\n"
            f"Таймфрейм: {self.config.period}\n"
            f"Цель продажи: цена покупки + {self.config.strategy.target_price_delta:.2f} USDT\n"
            f"Проверка рынка: каждые {self.config.poll_seconds:g} сек.\n"
            f"Комиссия: {self.config.strategy.fee_rate * 100:.3f}% за операцию\n"
            f"Статус: {state}\n\n"
            "Цикл: найти диапазон → подтвердить отскок → купить → дождаться цели.\n"
            "Реальные ордера и HTX API-ключи не используются.\n"
            "Команды: /status /position /current_market_price /stats /pause /resume /help"
        )

    def status_text(self) -> str:
        paused = self.storage.get("paused", False)
        last_price = float(self.storage.get("last_price", 0.0))
        last_candle = int(self.storage.get("last_candle_ts", 0))
        positions = self.storage.positions()
        state = self._state(paused, positions)
        total_pnl = sum(
            position.quantity * last_price * (1 - self.config.strategy.fee_rate) - self.config.strategy.capital
            for position in positions
        )
        text = (
            "📊 СТАТУС\n\n"
            f"Режим: SIMULATION\n"
            f"Состояние: {state}\n"
            f"Пара: {self.config.symbol.upper()}\n"
            f"Последняя цена: {last_price:.4f} USDT\n"
            f"Последняя закрытая свеча: {_time(last_candle) if last_candle else 'прогрев'}\n"
            f"Открытые позиции: {len(positions)} / {self.config.strategy.max_open_positions}\n"
            f"Задействованный капитал: {len(positions) * self.config.strategy.capital:.2f} / {self.total_capital:.2f} USDT\n"
            f"Суммарный текущий P&L: {total_pnl:+.2f} USDT"
        )
        if not positions:
            return text
        now = int(time.time())
        blocks = [self._position_block(position, last_price, now) for position in positions]
        return text + "\n\n" + "\n\n".join(blocks)

    def current_market_price_text(self) -> str:
        last_price = float(self.storage.get("last_price", 0.0))
        last_candle = int(self.storage.get("last_candle_ts", 0))
        return (
            "💵 ТЕКУЩАЯ РЫНОЧНАЯ ЦЕНА\n\n"
            f"Пара: {self.config.symbol.upper()}\n"
            f"Цена: {last_price:.4f} USDT\n"
            f"Последняя закрытая свеча: {_time(last_candle) if last_candle else 'прогрев'}"
        )

    def position_text(self, now: int | None = None) -> str:
        positions = self.storage.positions()
        if not positions:
            return "Открытой виртуальной позиции нет."
        now = now or int(time.time())
        fallback = positions[0].entry_price
        price = float(self.storage.get("last_price", fallback))
        blocks = [self._position_block(position, price, now) for position in positions]
        return "📌 ВИРТУАЛЬНЫЕ ПОЗИЦИИ\n\n" + "\n\n".join(blocks)

    def stats_text(self) -> str:
        stats = self.storage.stats()
        count = max(1, int(stats["count"]))
        text = (
            "📈 СТАТИСТИКА ТЕКУЩЕЙ СТРАТЕГИИ\n"
            "Без стопов — позиции удерживаются до цели.\n\n"
            f"Завершённых сделок: {stats['count']}\n"
            f"Прибыльных: {stats['wins']}\n"
            f"Реализованный P&L: {stats['profit']:.2f} USDT\n"
            f"Средняя сделка: {stats['average']:.2f} USDT\n"
            f"Максимальная открытая просадка (вся история): {abs(float(self.storage.get('max_drawdown', 0.0))):.2f} USDT\n\n"
            "Потенциал после входа:\n"
            f"Средний максимум после покупки: +{stats['avg_max_move']:.2f} USDT\n"
            f"Лучший максимум после покупки: +{stats['best_max_move']:.2f} USDT\n"
            f"Средняя просадка после покупки: {stats['avg_min_move']:.2f} USDT\n"
            f"Худшая просадка после покупки: {stats['worst_min_move']:.2f} USDT\n\n"
            "Достижение целей по закрытым сделкам:\n"
            f"+10 USDT: {stats['reached_10']} из {stats['count']} ({int(stats['reached_10']) / count * 100:.0f}%)\n"
            f"+15 USDT: {stats['reached_15']} из {stats['count']} ({int(stats['reached_15']) / count * 100:.0f}%)\n"
            f"+20 USDT: {stats['reached_20']} из {stats['count']} ({int(stats['reached_20']) / count * 100:.0f}%)\n"
            f"+25 USDT: {stats['reached_25']} из {stats['count']} ({int(stats['reached_25']) / count * 100:.0f}%)\n\n"
            f"Рекомендация: {_target_recommendation(stats)}"
        )
        if int(stats["archived_count"]):
            text += (
                "\n\n🗄 АРХИВ ЭКСПЕРИМЕНТАЛЬНОЙ СТРАТЕГИИ\n"
                "Не учитывается в показателях выше.\n"
                f"Сделок: {stats['archived_count']}\n"
                f"Прибыльных: {stats['archived_wins']}\n"
                f"Закрытий по стопу: {stats['archived_stops']}\n"
                f"Архивный P&L: {stats['archived_profit']:.2f} USDT"
            )
        return text

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
                    "/current_market_price — текущая рыночная цена\n"
                    "/stats — результаты\n"
                    "/pause — запрет новых покупок\n"
                    "/resume — продолжить поиск входа\n\n"
                    "Пауза не отменяет виртуальную продажу уже открытой позиции."
                )
            elif command == "/status":
                response = self.status_text()
            elif command == "/position":
                response = self.position_text()
            elif command == "/current_market_price":
                response = self.current_market_price_text()
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
        self.storage.close_position(position.id, timestamp, position.target_price, sell_fee, net)
        cooldown_until = timestamp + self.config.strategy.cooldown_candles * self.period_seconds
        self.storage.set("cooldown_until", cooldown_until)
        stats = self.storage.stats()
        self._send(
            "✅ ВИРТУАЛЬНАЯ ПРОДАЖА\n\n"
            f"Позиция: #{position.id}\n"
            f"Время: {_time(timestamp)}\n"
            f"Покупка: {position.entry_price:.4f} USDT\n"
            f"Продажа: {position.target_price:.4f} USDT\n"
            f"Разница цены: +{position.target_price - position.entry_price:.2f} USDT\n"
            f"Количество: {position.quantity:.6f} {self.base_asset}\n"
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
        self.storage.set_alert_level(position.id, level)
        self._send(
            "⚠️ ВИРТУАЛЬНАЯ ПОЗИЦИЯ В ПРОСАДКЕ\n\n"
            f"Позиция: #{position.id}\n"
            f"Покупка: {position.entry_price:.4f} USDT\n"
            f"Текущая цена: {price:.4f} USDT\n"
            f"Просадка цены: {drawdown_pct * 100:.2f}%\n"
            f"Текущий P&L: {pnl:+.2f} USDT\n"
            f"Цель продажи не изменена: {position.target_price:.4f} USDT\n"
            f"Удержание: {_duration(timestamp - position.entry_time)}",
            "drawdown",
        )

    def _track_positions_for_candle(self, candle: Candle, timestamp: int) -> bool:
        sold = False
        for position in self.storage.positions():
            updated = self.storage.update_position_extremes(position.id, candle.high, candle.low) or position
            entry_candle_ts = updated.entry_time // self.period_seconds * self.period_seconds
            if candle.timestamp >= entry_candle_ts and candle.high >= updated.target_price:
                self._sell(updated, timestamp)
                sold = True
                continue
            self._check_drawdown(updated, candle.close, timestamp)
        return sold

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
            current = candles[-1]
            if self._track_positions_for_candle(current, now):
                return "sold"
            return "holding" if self.storage.positions() else "idle"

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

        # Existing virtual sells remain active during downtime and pause.
        sold_position = False
        for candle in new_candles:
            sold_position = self._track_positions_for_candle(candle, candle.timestamp) or sold_position
            self.storage.set("last_candle_ts", candle.timestamp)

        if sold_position:
            return "sold"

        current = candles[-1]
        if current.timestamp >= cutoff and self._track_positions_for_candle(current, now):
            return "sold"

        positions = self.storage.positions()
        has_positions = bool(positions)
        if len(positions) >= self.config.strategy.max_open_positions:
            return "holding"
        if self.storage.get("paused", False):
            return "holding" if has_positions else "paused"
        if new_candles[-1].timestamp < int(self.storage.get("cooldown_until", 0)):
            return "holding" if has_positions else "cooldown"

        signal_candle = new_candles[-1]
        active = next((c for c in candles if c.timestamp > signal_candle.timestamp), None)
        fill_reference = active.open if active else signal_candle.close
        history = [candle for candle in closed if candle.timestamp <= signal_candle.timestamp]
        setup = detect_entry_setup(history, fill_reference, self.config.strategy)
        if setup is None:
            return "holding" if has_positions else "analyzing"

        entry_price = setup.entry_price
        can_open, reason = self._can_open_new_position(entry_price, positions)
        if not can_open:
            self.storage.set("last_entry_skip_reason", reason)
            return "holding" if has_positions else "analyzing"

        buy_fee = self.config.strategy.capital * self.config.strategy.fee_rate
        quantity = (self.config.strategy.capital - buy_fee) / entry_price
        target = setup.target_price

        position = StoredPosition(
            entry_time=active.timestamp if active else now,
            entry_price=entry_price,
            quantity=quantity,
            buy_fee=buy_fee,
            target_price=target,
            last_alert_level=0.0,
            max_price=entry_price,
            min_price=entry_price,
        )
        position = self.storage.open_position(position)
        expected_proceeds = quantity * target * (1 - self.config.strategy.fee_rate)
        self._send(
            "🟢 ВИРТУАЛЬНАЯ ПОКУПКА\n\n"
            f"Позиция: #{position.id}\n"
            f"Время: {_time(position.entry_time)}\n"
            f"Цена: {entry_price:.4f} USDT\n"
            f"Количество: {quantity:.6f} {self.base_asset}\n"
            f"Рабочий капитал: {self.config.strategy.capital:.2f} USDT\n"
            f"Комиссия покупки: {buy_fee:.2f} USDT\n\n"
            f"Поддержка диапазона: {setup.support:.4f} USDT\n"
            f"Сопротивление диапазона: {setup.resistance:.4f} USDT\n"
            f"Открытые позиции после покупки: {len(self.storage.positions())} / {self.config.strategy.max_open_positions}\n"
            f"Виртуальная продажа: {target:.4f} USDT (+{target - entry_price:.2f})\n"
            f"Ожидаемая валовая прибыль от движения цены: {quantity * (target - entry_price):.2f} USDT\n"
            f"Ожидаемая чистая прибыль: {expected_proceeds - self.config.strategy.capital:.2f} USDT\n\n"
            "Вход подтверждён отскоком в нижней части торгового диапазона. "
            "Если цена снизится, позиция останется открытой до восстановления.",
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
