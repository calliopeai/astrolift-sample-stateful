"""SQLite-backed key/value store.

The whole point of this fixture is that the data lives at ``STORE_PATH``,
which the manifest mounts onto a PersistentVolumeClaim. The file is created
lazily on first write, the schema is created idempotently, and every
connection enables WAL so a hard pod kill mid-write still recovers cleanly
(important for the campaign's "state survives restart" check).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

DEFAULT_STORE_PATH = "/var/lib/store/data.db"


def store_path() -> str:
    """Resolve the on-disk store path from the environment.

    Defaults to the PVC mount point declared in ``manifests/stateful.toml``.
    """
    return os.environ.get("STORE_PATH", DEFAULT_STORE_PATH)


class KVStore:
    """A tiny persistent key/value store over SQLite.

    Thread-safe for the single-process uvicorn worker this fixture runs:
    a lock serializes writes and each call opens a short-lived connection,
    so there is no cross-thread connection sharing.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        # Create the parent directory if it does not exist yet. On a real
        # PVC the mount point exists; locally (and in unit tests) it may not.
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def path(self) -> str:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    def put(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kv (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value),
            )

    def get(self, key: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def list(self) -> dict[str, str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM kv ORDER BY key").fetchall()
        return {k: v for k, v in rows}

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM kv").fetchone()
        return int(row[0])
