from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import StrategyConfig


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} должен быть true/false")


@dataclass(frozen=True)
class LiveConfig:
    telegram_token: str
    telegram_chat_id: int
    symbol: str
    period: str
    poll_seconds: float
    database_path: Path
    strategy: StrategyConfig


def load_live_config(env_file: str | Path = ".env") -> LiveConfig:
    load_dotenv(env_file)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        raise ValueError("В .env не задан TELEGRAM_BOT_TOKEN")
    if not chat_id:
        raise ValueError("В .env не задан TELEGRAM_CHAT_ID")

    strategy = StrategyConfig(
        capital=_float("CAPITAL", 1800.0),
        max_open_positions=_int("MAX_OPEN_POSITIONS", 2),
        min_position_distance_usdt=_float("MIN_POSITION_DISTANCE_USDT", 70.0),
        fee_rate=_float("FEE_RATE", 0.002),
        slippage_rate=_float("SLIPPAGE_RATE", 0.0002),
        target_price_delta=_float("TARGET_PRICE_DELTA", 10.0),
        lookback=_int("LOOKBACK", 90),
        min_range_pct=_float("MIN_RANGE_PCT", 0.008),
        max_slope_pct=_float("MAX_SLOPE_PCT", 0.004),
        entry_zone=_float("ENTRY_ZONE", 0.30),
        exit_zone=_float("EXIT_ZONE", 0.65),
        min_touches=_int("MIN_TOUCHES", 2),
        min_net_profit_pct=_float("MIN_NET_PROFIT_PCT", 0.0035),
        cooldown_candles=_int("COOLDOWN_CANDLES", 0),
        reversal_enabled=_bool("REVERSAL_ENABLED", True),
        reversal_lookback=_int("REVERSAL_LOOKBACK", 60),
        reversal_confirmation_candles=_int("REVERSAL_CONFIRMATION_CANDLES", 15),
        reversal_drop_pct=_float("REVERSAL_DROP_PCT", 0.004),
        reversal_rebound_pct=_float("REVERSAL_REBOUND_PCT", 0.0025),
        reversal_max_rebound_pct=_float("REVERSAL_MAX_REBOUND_PCT", 0.008),
        reversal_volume_lookback=_int("REVERSAL_VOLUME_LOOKBACK", 20),
        reversal_volume_multiplier=_float("REVERSAL_VOLUME_MULTIPLIER", 2.0),
        reversal_stop_buffer_pct=_float("REVERSAL_STOP_BUFFER_PCT", 0.001),
        reversal_reward_risk=_float("REVERSAL_REWARD_RISK", 2.0),
    )
    if strategy.capital <= 0:
        raise ValueError("CAPITAL должен быть больше нуля")
    if strategy.max_open_positions < 1:
        raise ValueError("MAX_OPEN_POSITIONS должен быть не меньше 1")
    if strategy.min_position_distance_usdt < 0:
        raise ValueError("MIN_POSITION_DISTANCE_USDT не должен быть отрицательным")
    if not 0 <= strategy.fee_rate < 0.1:
        raise ValueError("FEE_RATE должен быть от 0 до 0.1")
    if strategy.target_price_delta <= 0:
        raise ValueError("TARGET_PRICE_DELTA должен быть больше нуля")
    if strategy.lookback < 20:
        raise ValueError("LOOKBACK должен быть не меньше 20")
    if not 0 < strategy.entry_zone < strategy.exit_zone < 1:
        raise ValueError("Должно выполняться 0 < ENTRY_ZONE < EXIT_ZONE < 1")
    if strategy.reversal_lookback < 20:
        raise ValueError("REVERSAL_LOOKBACK должен быть не меньше 20")
    if not 1 <= strategy.reversal_confirmation_candles < strategy.reversal_lookback:
        raise ValueError("REVERSAL_CONFIRMATION_CANDLES должен быть меньше REVERSAL_LOOKBACK")
    if not 0 < strategy.reversal_drop_pct < 0.1:
        raise ValueError("REVERSAL_DROP_PCT должен быть от 0 до 0.1")
    if not 0 < strategy.reversal_rebound_pct < strategy.reversal_max_rebound_pct < 0.1:
        raise ValueError(
            "Должно выполняться 0 < REVERSAL_REBOUND_PCT < REVERSAL_MAX_REBOUND_PCT < 0.1"
        )
    if not 2 <= strategy.reversal_volume_lookback < strategy.reversal_lookback:
        raise ValueError("REVERSAL_VOLUME_LOOKBACK должен быть меньше REVERSAL_LOOKBACK")
    if strategy.reversal_volume_multiplier <= 1:
        raise ValueError("REVERSAL_VOLUME_MULTIPLIER должен быть больше 1")
    if not 0 <= strategy.reversal_stop_buffer_pct < 0.1:
        raise ValueError("REVERSAL_STOP_BUFFER_PCT должен быть от 0 до 0.1")
    if strategy.reversal_reward_risk < 1:
        raise ValueError("REVERSAL_REWARD_RISK должен быть не меньше 1")
    poll_seconds = _float("POLL_SECONDS", 10.0)
    if poll_seconds < 5:
        raise ValueError("POLL_SECONDS не должен быть меньше 5 секунд")
    return LiveConfig(
        telegram_token=token,
        telegram_chat_id=int(chat_id),
        symbol=os.getenv("SYMBOL", "ethusdt").lower(),
        period=os.getenv("PERIOD", "1min"),
        poll_seconds=poll_seconds,
        database_path=Path(os.getenv("DATABASE_PATH", "data/eth_live_simulator.db")),
        strategy=strategy,
    )
