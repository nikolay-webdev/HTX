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
    max_price: float | None = None
    min_price: float | None = None
    id: int = 1


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
                id INTEGER PRIMARY KEY,
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
        self._migrate_single_position_table()
        self._add_column("position", "max_price", "REAL")
        self._add_column("position", "min_price", "REAL")
        self._add_column("trades", "max_price", "REAL")
        self._add_column("trades", "min_price", "REAL")
        # Keep strategy metadata so experiments can remain in the database
        # without affecting the statistics for the active range strategy.
        self._add_column("trades", "entry_kind", "TEXT NOT NULL DEFAULT 'range'")
        self._add_column("trades", "exit_reason", "TEXT NOT NULL DEFAULT 'target'")
        self.connection.commit()

    def _migrate_single_position_table(self) -> None:
        row = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'position'"
        ).fetchone()
        if row is None or "CHECK (id = 1)" not in row["sql"]:
            return
        self.connection.executescript(
            """
            ALTER TABLE position RENAME TO position_single_backup;
            CREATE TABLE position (
                id INTEGER PRIMARY KEY,
                entry_time INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                buy_fee REAL NOT NULL,
                target_price REAL NOT NULL,
                last_alert_level REAL NOT NULL DEFAULT 0
            );
            INSERT INTO position(
                id, entry_time, entry_price, quantity, buy_fee, target_price, last_alert_level
            )
            SELECT id, entry_time, entry_price, quantity, buy_fee, target_price, last_alert_level
            FROM position_single_backup;
            DROP TABLE position_single_backup;
            """
        )

    def _add_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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

    def _position_from_row(self, row: sqlite3.Row) -> StoredPosition:
        return StoredPosition(
            entry_time=row["entry_time"],
            entry_price=row["entry_price"],
            quantity=row["quantity"],
            buy_fee=row["buy_fee"],
            target_price=row["target_price"],
            last_alert_level=row["last_alert_level"],
            max_price=row["max_price"] if row["max_price"] is not None else row["entry_price"],
            min_price=row["min_price"] if row["min_price"] is not None else row["entry_price"],
            id=row["id"],
        )

    def positions(self) -> list[StoredPosition]:
        rows = self.connection.execute("SELECT * FROM position ORDER BY id").fetchall()
        return [self._position_from_row(row) for row in rows]

    def position(self) -> StoredPosition | None:
        row = self.connection.execute("SELECT * FROM position ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        return self._position_from_row(row)

    def open_position(self, position: StoredPosition) -> StoredPosition:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO position(
                    entry_time, entry_price, quantity, buy_fee,
                    target_price, last_alert_level, max_price, min_price
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    position.entry_time,
                    position.entry_price,
                    position.quantity,
                    position.buy_fee,
                    position.target_price,
                    position.last_alert_level,
                    position.max_price if position.max_price is not None else position.entry_price,
                    position.min_price if position.min_price is not None else position.entry_price,
                ),
            )
            position_id = int(cursor.lastrowid)
        return StoredPosition(
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            quantity=position.quantity,
            buy_fee=position.buy_fee,
            target_price=position.target_price,
            last_alert_level=position.last_alert_level,
            max_price=position.max_price if position.max_price is not None else position.entry_price,
            min_price=position.min_price if position.min_price is not None else position.entry_price,
            id=position_id,
        )

    def set_alert_level(self, position_id: int, level: float) -> None:
        self.connection.execute(
            "UPDATE position SET last_alert_level = ? WHERE id = ?",
            (level, position_id),
        )
        self.connection.commit()

    def update_position_extremes(self, position_id: int, high: float, low: float) -> StoredPosition | None:
        row = self.connection.execute("SELECT * FROM position WHERE id = ?", (position_id,)).fetchone()
        if row is None:
            return None
        position = self._position_from_row(row)
        if position is None:
            return None
        max_price = max(position.max_price or position.entry_price, high)
        min_price = min(position.min_price or position.entry_price, low)
        self.connection.execute(
            "UPDATE position SET max_price = ?, min_price = ? WHERE id = ?",
            (max_price, min_price, position_id),
        )
        self.connection.commit()
        return StoredPosition(
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            quantity=position.quantity,
            buy_fee=position.buy_fee,
            target_price=position.target_price,
            last_alert_level=position.last_alert_level,
            max_price=max_price,
            min_price=min_price,
            id=position.id,
        )

    def close_position(self, position_id: int, exit_time: int, exit_price: float, sell_fee: float, net_profit: float) -> None:
        with self.connection:
            row = self.connection.execute("SELECT * FROM position WHERE id = ?", (position_id,)).fetchone()
            if row is None:
                raise RuntimeError("Нет открытой позиции")
            self.connection.execute(
                """INSERT INTO trades(
                    entry_time, exit_time, entry_price, exit_price, quantity,
                    buy_fee, sell_fee, net_profit, max_price, min_price
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["entry_time"], exit_time, row["entry_price"], exit_price,
                    row["quantity"], row["buy_fee"], sell_fee, net_profit,
                    row["max_price"] if row["max_price"] is not None else max(row["entry_price"], exit_price),
                    row["min_price"] if row["min_price"] is not None else min(row["entry_price"], exit_price),
                ),
            )
            self.connection.execute("DELETE FROM position WHERE id = ?", (position_id,))

    def stats(self) -> dict[str, float | int]:
        row = self.connection.execute(
            """SELECT COALESCE(SUM(CASE WHEN entry_kind = 'range' THEN 1 ELSE 0 END), 0) count,
                      COALESCE(SUM(CASE WHEN entry_kind = 'range' THEN net_profit ELSE 0 END), 0) profit,
                      COALESCE(AVG(CASE WHEN entry_kind = 'range' THEN net_profit END), 0) average,
                      COALESCE(SUM(CASE WHEN entry_kind = 'range' AND net_profit > 0 THEN 1 ELSE 0 END), 0) wins,
                      COALESCE(AVG(CASE WHEN entry_kind = 'range' THEN COALESCE(max_price, exit_price) - entry_price END), 0) avg_max_move,
                      COALESCE(MAX(CASE WHEN entry_kind = 'range' THEN COALESCE(max_price, exit_price) - entry_price END), 0) best_max_move,
                      COALESCE(AVG(CASE WHEN entry_kind = 'range' THEN COALESCE(min_price, entry_price) - entry_price END), 0) avg_min_move,
                      COALESCE(MIN(CASE WHEN entry_kind = 'range' THEN COALESCE(min_price, entry_price) - entry_price END), 0) worst_min_move,
                      COALESCE(SUM(CASE WHEN entry_kind = 'range' AND COALESCE(max_price, exit_price) - entry_price >= 10 THEN 1 ELSE 0 END), 0) reached_10,
                      COALESCE(SUM(CASE WHEN entry_kind = 'range' AND COALESCE(max_price, exit_price) - entry_price >= 15 THEN 1 ELSE 0 END), 0) reached_15,
                      COALESCE(SUM(CASE WHEN entry_kind = 'range' AND COALESCE(max_price, exit_price) - entry_price >= 20 THEN 1 ELSE 0 END), 0) reached_20,
                      COALESCE(SUM(CASE WHEN entry_kind = 'range' AND COALESCE(max_price, exit_price) - entry_price >= 25 THEN 1 ELSE 0 END), 0) reached_25,
                      COALESCE(SUM(CASE WHEN entry_kind != 'range' THEN 1 ELSE 0 END), 0) archived_count,
                      COALESCE(SUM(CASE WHEN entry_kind != 'range' AND net_profit > 0 THEN 1 ELSE 0 END), 0) archived_wins,
                      COALESCE(SUM(CASE WHEN entry_kind != 'range' THEN net_profit ELSE 0 END), 0) archived_profit,
                      COALESCE(SUM(CASE WHEN entry_kind != 'range' AND exit_reason = 'stop' THEN 1 ELSE 0 END), 0) archived_stops
               FROM trades"""
        ).fetchone()
        return dict(row)

    def event(self, timestamp: int, kind: str, message: str) -> None:
        self.connection.execute(
            "INSERT INTO events(created_at, kind, message) VALUES(?, ?, ?)",
            (timestamp, kind, message),
        )
        self.connection.commit()
