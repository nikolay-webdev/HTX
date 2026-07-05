from __future__ import annotations

import csv
import json
import ssl
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Candle

HTX_KLINES_URL = "https://api.huobi.pro/market/history/kline"


def _ssl_context() -> ssl.SSLContext:
    # Some standalone macOS Python builds do not inherit the Keychain roots.
    # Prefer the OS CA bundle when it exists; certificate verification remains on.
    system_bundle = Path("/etc/ssl/cert.pem")
    return ssl.create_default_context(cafile=str(system_bundle) if system_bundle.exists() else None)


def fetch_htx_candles(symbol: str = "solusdt", period: str = "1min", size: int = 2000) -> list[Candle]:
    if not 1 <= size <= 2000:
        raise ValueError("HTX size должен быть от 1 до 2000")
    query = urlencode({"symbol": symbol.lower(), "period": period, "size": size})
    request = Request(f"{HTX_KLINES_URL}?{query}", headers={"User-Agent": "htx-range-simulator/0.1"})
    with urlopen(request, timeout=20, context=_ssl_context()) as response:
        payload = json.load(response)
    if payload.get("status") != "ok":
        raise RuntimeError(f"HTX API error: {payload}")
    candles = [
        Candle(
            timestamp=int(item["id"]),
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            volume=float(item.get("vol", 0.0)),
        )
        for item in payload["data"]
    ]
    return sorted(candles, key=lambda candle: candle.timestamp)


def _parse_timestamp(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def read_csv(path: str | Path) -> list[Candle]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"CSV должен содержать столбцы: {', '.join(sorted(required))}")
        candles = [
            Candle(
                timestamp=_parse_timestamp(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in reader
        ]
    return sorted(candles, key=lambda candle: candle.timestamp)


def write_csv(candles: list[Candle], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for candle in candles:
            writer.writerow([
                candle.timestamp,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            ])
