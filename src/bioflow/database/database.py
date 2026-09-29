"""SQLite connection management: schema creation, version check, transactions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from bioflow.core.exceptions import ConfigurationError
from bioflow.database.models import SCHEMA, SCHEMA_VERSION

IN_MEMORY = ":memory:"


class Database:
    """One SQLite database file (or ``":memory:"``) holding any number of runs."""

    def __init__(self, path: Path | str = IN_MEMORY) -> None:
        self.path = str(path)
        if self.path != IN_MEMORY:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(SCHEMA)
            row = connection.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] != SCHEMA_VERSION:
                raise ConfigurationError(
                    f"{self.path}: database schema version {row['version']}, this code expects {SCHEMA_VERSION}"
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on any exception, so a run is saved completely or not at all."""
        try:
            yield self._connection
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
