from __future__ import annotations

from .models import BacktestResult


def _duration(seconds: int) -> str:
    minutes = seconds // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return f"{days}T {hours}Std {minutes}Min" if days else f"{hours}Std {minutes}Min"


def format_report(result: BacktestResult) -> str:
    wins = sum(trade.net_profit > 0 for trade in result.trades)
    average = result.realized_profit / len(result.trades) if result.trades else 0.0
    exposure = 100 * result.exposure_seconds / result.elapsed_seconds if result.elapsed_seconds else 0.0
    lines = [
        "Simulationsergebnis",
        f"Abgeschlossene Trades: {len(result.trades)}",
        f"Gewinnbringende Trades: {wins}",
        f"Realisierter P&L: {result.realized_profit:.2f} USDT",
        f"Nicht realisierter P&L: {result.unrealized_profit:.2f} USDT",
        f"Gesamter P&L: {result.total_profit:.2f} USDT",
        f"Durchschnittlicher Trade: {average:.2f} USDT",
        f"Maximaler Drawdown: {result.max_drawdown:.2f} USDT",
        f"Kapital war {exposure:.1f}% der Zeit investiert",
    ]
    if result.trades:
        longest = max(trade.holding_seconds for trade in result.trades)
        lines.append(f"Längste abgeschlossene Haltedauer: {_duration(longest)}")
    if result.position:
        lines.extend([
            "Offene Position: JA",
            f"Kaufpreis: {result.position.entry_price:.4f}",
            f"Letzter Preis: {result.last_price:.4f}",
            f"Verkaufsziel: {result.position.target_price:.4f}",
            f"Aktuelle Haltedauer: {_duration(result.position_age_seconds)}",
        ])
    else:
        lines.append("Offene Position: nein")
    return "\n".join(lines)
