"""Minimal standard-library SQLite persistence adapter."""

import sqlite3
from pathlib import Path


class SQLitePersistence:
    """Own a SQLite connection without defining pre-M1 domain schemas."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self._database = str(database)
        self._connection: sqlite3.Connection | None = None

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    def initialize(self) -> None:
        if self._connection is not None:
            raise RuntimeError("SQLite persistence is already initialized")
        connection = sqlite3.connect(self._database)
        connection.execute("PRAGMA foreign_keys = ON")
        self._connection = connection

    def close(self) -> None:
        if self._connection is None:
            return
        self._connection.close()
        self._connection = None
