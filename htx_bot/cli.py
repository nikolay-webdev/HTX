from __future__ import annotations

import argparse
import os

from .config import load_dotenv, load_live_config
from .data import fetch_htx_candles, read_csv, write_csv
from .live import LiveSimulator
from .models import StrategyConfig
from .report import format_report
from .storage import Storage
from .strategy import run_backtest
from .telegram import TelegramClient


def _config(args: argparse.Namespace) -> StrategyConfig:
    return StrategyConfig(
        capital=args.capital,
        fee_rate=args.fee_rate,
        slippage_rate=args.slippage_rate,
        target_price_delta=args.target_price_delta,
        lookback=args.lookback,
        min_range_pct=args.min_range_pct,
        max_slope_pct=args.max_slope_pct,
        entry_zone=args.entry_zone,
        exit_zone=args.exit_zone,
        min_touches=args.min_touches,
        min_net_profit_pct=args.min_net_profit_pct,
        cooldown_candles=args.cooldown_candles,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ETH/USDT spot range strategy simulator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Загрузить последние свечи HTX в CSV")
    fetch.add_argument("--symbol", default="ethusdt")
    fetch.add_argument("--period", default="1min")
    fetch.add_argument("--size", type=int, default=2000)
    fetch.add_argument("--output", required=True)

    backtest = subparsers.add_parser("backtest", help="Запустить симуляцию")
    source = backtest.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="CSV со свечами")
    source.add_argument("--fetch", action="store_true", help="Взять последние свечи с HTX")
    backtest.add_argument("--symbol", default="ethusdt")
    backtest.add_argument("--period", default="1min")
    backtest.add_argument("--size", type=int, default=2000)
    backtest.add_argument("--capital", type=float, default=1800.0)
    backtest.add_argument("--fee-rate", type=float, default=0.002)
    backtest.add_argument("--slippage-rate", type=float, default=0.0002)
    backtest.add_argument("--target-price-delta", type=float, default=10.0)
    backtest.add_argument("--lookback", type=int, default=120)
    backtest.add_argument("--min-range-pct", type=float, default=0.012)
    backtest.add_argument("--max-slope-pct", type=float, default=0.004)
    backtest.add_argument("--entry-zone", type=float, default=0.30)
    backtest.add_argument("--exit-zone", type=float, default=0.65)
    backtest.add_argument("--min-touches", type=int, default=2)
    backtest.add_argument("--min-net-profit-pct", type=float, default=0.0035)
    backtest.add_argument("--cooldown-candles", type=int, default=0)

    live = subparsers.add_parser("live", help="Запустить постоянный dry-run с Telegram")
    live.add_argument("--env", default=".env", help="Путь к файлу настроек")

    telegram_info = subparsers.add_parser(
        "telegram-info", help="Проверить токен и найти chat_id после сообщения боту"
    )
    telegram_info.add_argument("--env", default=".env")

    telegram_test = subparsers.add_parser("telegram-test", help="Отправить тестовое сообщение")
    telegram_test.add_argument("--env", default=".env")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "fetch":
        candles = fetch_htx_candles(args.symbol, args.period, args.size)
        write_csv(candles, args.output)
        print(f"Сохранено свечей: {len(candles)} -> {args.output}")
        return

    if args.command == "telegram-info":
        load_dotenv(args.env)
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit("В .env не задан TELEGRAM_BOT_TOKEN")
        telegram = TelegramClient(token, 0)
        bot = telegram.check()
        updates = telegram.updates()
        chats = {
            int(update["message"]["chat"]["id"]): update["message"]["chat"].get("type", "")
            for update in updates
            if update.get("message", {}).get("chat", {}).get("id") is not None
        }
        print(f"Бот: @{bot.get('username', 'unknown')}")
        if chats:
            for chat_id, chat_type in chats.items():
                print(f"TELEGRAM_CHAT_ID={chat_id} ({chat_type})")
        else:
            print("Сообщений нет. Отправьте боту /start и повторите команду.")
        return

    if args.command in {"telegram-test", "live"}:
        config = load_live_config(args.env)
        telegram = TelegramClient(config.telegram_token, config.telegram_chat_id)
        if args.command == "telegram-test":
            bot = telegram.check()
            telegram.send("✅ Telegram подключён к безопасному HTX-симулятору.")
            print(f"Сообщение отправлено через @{bot.get('username', 'unknown')}")
            return
        storage = Storage(config.database_path)
        try:
            LiveSimulator(config, storage, telegram).run()
        finally:
            storage.close()
        return

    candles = (
        fetch_htx_candles(args.symbol, args.period, args.size)
        if args.fetch
        else read_csv(args.input)
    )
    result = run_backtest(candles, _config(args))
    print(format_report(result))
