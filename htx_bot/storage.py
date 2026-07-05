from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredPosition:
    entry_time: int
    entry_price: float
    quantity: float
    buy_fee: float
    target_price: float
    last_alert_level: float


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS position (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                entry_time INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                buy_fee REAL NOT NULL,
                target_price REAL NOT NULL,
                last_alert_level REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS trades (
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
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def get(self, key: str, default=None):
        row = self.connection.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def set(self, key: str, value) -> None:
        self.connection.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        self.connection.commit()

    def position(self) -> StoredPosition | None:
        row = self.connection.execute("SELECT * FROM position WHERE id = 1").fetchone()
        if row is None:
            return None
        return StoredPosition(
            entry_time=row["entry_time"],
            entry_price=row["entry_price"],
            quantity=row["quantity"],
            buy_fee=row["buy_fee"],
            target_price=row["target_price"],
            last_alert_level=row["last_alert_level"],
        )

    def open_position(self, position: StoredPosition) -> None:
        with self.connection:
            existing = self.connection.execute("SELECT 1 FROM position WHERE id = 1").fetchone()
            if existing:
                raise RuntimeError("Позиция уже открыта")
            self.connection.execute(
                "INSERT INTO position VALUES(1, ?, ?, ?, ?, ?, ?)",
                (
                    position.entry_time,
                    position.entry_price,
                    position.quantity,
                    position.buy_fee,
                    position.target_price,
                    position.last_alert_level,
                ),
            )

    def set_alert_level(self, level: float) -> None:
        self.connection.execute("UPDATE position SET last_alert_level = ? WHERE id = 1", (level,))
        self.connection.commit()

    def close_position(self, exit_time: int, exit_price: float, sell_fee: float, net_profit: float) -> None:
        with self.connection:
            row = self.connection.execute("SELECT * FROM position WHERE id = 1").fetchone()
            if row is None:
                raise RuntimeError("Нет открытой позиции")
            self.connection.execute(
                """INSERT INTO trades(
                    entry_time, exit_time, entry_price, exit_price, quantity,
                    buy_fee, sell_fee, net_profit
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["entry_time"], exit_time, row["entry_price"], exit_price,
                    row["quantity"], row["buy_fee"], sell_fee, net_profit,
                ),
            )
            self.connection.execute("DELETE FROM position WHERE id = 1")

    def stats(self) -> dict[str, float | int]:
        row = self.connection.execute(
            """SELECT COUNT(*) count,
                      COALESCE(SUM(net_profit), 0) profit,
                      COALESCE(AVG(net_profit), 0) average,
                      COALESCE(SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END), 0) wins
               FROM trades"""
        ).fetchone()
        return dict(row)

    def event(self, timestamp: int, kind: str, message: str) -> None:
        self.connection.execute(
            "INSERT INTO events(created_at, kind, message) VALUES(?, ?, ?)",
            (timestamp, kind, message),
        )
        self.connection.commit()

