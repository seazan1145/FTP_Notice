from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .utils import utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS observed_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_name TEXT NOT NULL,
    remote_dir TEXT NOT NULL,
    remote_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_size_change_at TEXT,
    is_stable INTEGER NOT NULL DEFAULT 0,
    is_notified INTEGER NOT NULL DEFAULT 0,
    notified_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(connection_name, remote_path)
);
"""


class MonitorDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def get_observed_file(self, connection_name: str, remote_path: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM observed_files WHERE connection_name=? AND remote_path=?",
            (connection_name, remote_path),
        )
        return cur.fetchone()

    def insert_candidate(self, payload: dict[str, Any]) -> None:
        now = utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO observed_files (
                connection_name, remote_dir, remote_path, file_name, file_size,
                first_seen_at, last_seen_at, last_size_change_at,
                is_stable, is_notified, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
            """,
            (
                payload["connection_name"],
                payload["remote_dir"],
                payload["remote_path"],
                payload["file_name"],
                payload["file_size"],
                now,
                now,
                now,
                now,
                now,
            ),
        )
        self._conn.commit()

    def update_seen(self, record_id: int, file_size: int, size_changed: bool, is_stable: bool) -> None:
        now = utc_now_iso()
        if size_changed:
            self._conn.execute(
                """
                UPDATE observed_files
                SET file_size=?, last_seen_at=?, last_size_change_at=?, is_stable=?, updated_at=?
                WHERE id=?
                """,
                (file_size, now, now, int(is_stable), now, record_id),
            )
        else:
            self._conn.execute(
                """
                UPDATE observed_files
                SET last_seen_at=?, is_stable=?, updated_at=?
                WHERE id=?
                """,
                (now, int(is_stable), now, record_id),
            )
        self._conn.commit()

    def mark_notified(self, record_id: int) -> None:
        now = utc_now_iso()
        self._conn.execute(
            "UPDATE observed_files SET is_notified=1, notified_at=?, updated_at=? WHERE id=?",
            (now, now, record_id),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
