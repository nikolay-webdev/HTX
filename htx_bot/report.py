from __future__ import annotations

from .models import BacktestResult


def _duration(seconds: int) -> str:
    minutes = seconds // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return f"{days}д {hours}ч {minutes}м" if days else f"{hours}ч {minutes}м"


def format_report(result: BacktestResult) -> str:
    wins = sum(trade.net_profit > 0 for trade in result.trades)
    average = result.realized_profit / len(result.trades) if result.trades else 0.0
    exposure = 100 * result.exposure_seconds / result.elapsed_seconds if result.elapsed_seconds else 0.0
    lines = [
        "Результат симуляции",
        f"Завершённых сделок: {len(result.trades)}",
        f"Прибыльных сделок: {wins}",
        f"Реализованный P&L: {result.realized_profit:.2f} USDT",
        f"Нереализованный P&L: {result.unrealized_profit:.2f} USDT",
        f"Итоговый P&L: {result.total_profit:.2f} USDT",
        f"Средняя прибыль сделки: {average:.2f} USDT",
        f"Максимальная просадка: {result.max_drawdown:.2f} USDT",
        f"Капитал был в позиции: {exposure:.1f}% времени",
    ]
    if result.trades:
        longest = max(trade.holding_seconds for trade in result.trades)
        lines.append(f"Максимальное закрытое удержание: {_duration(longest)}")
    if result.position:
        lines.extend([
            "Открытая позиция: ДА",
            f"Цена покупки: {result.position.entry_price:.4f}",
            f"Последняя цена: {result.last_price:.4f}",
            f"Цель продажи: {result.position.target_price:.4f}",
            f"Текущее время удержания: {_duration(result.position_age_seconds)}",
        ])
    else:
        lines.append("Открытая позиция: нет")
    return "\n".join(lines)
