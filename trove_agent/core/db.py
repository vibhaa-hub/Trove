"""Thin database adapter: SQLite for development, PostgreSQL for production.

Application code keeps writing portable SQL with ``?`` placeholders. For
PostgreSQL this module translates the few SQLite-isms the code uses:

* ``?``                         -> ``%s``
* ``INSERT OR IGNORE INTO ...``  -> ``INSERT INTO ... ON CONFLICT DO NOTHING``
* ``BEGIN IMMEDIATE`` / ``PRAGMA`` -> row lock no-op / ignored

Rows support both ``row["column"]`` and ``row[0]`` access on both backends.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # optional in development
    import psycopg
    from psycopg import errors as pg_errors
except ImportError:  # pragma: no cover - exercised only without psycopg
    psycopg = None
    pg_errors = None

IntegrityError: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,) + (
    (pg_errors.IntegrityError,) if pg_errors else ())


def is_postgres(url: str) -> bool:
    return str(url).startswith(("postgres://", "postgresql://"))


class Row(tuple):
    """Tuple with column-name access, compatible with sqlite3.Row usage."""

    _index: dict[str, int]

    def __new__(cls, values: Sequence[Any], index: dict[str, int]) -> "Row":
        row = super().__new__(cls, values)
        row._index = index
        return row

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, str):
            return tuple.__getitem__(self, self._index[key])
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._index)


def _pg_row_factory(cursor: Any):
    names = [d.name for d in cursor.description] if cursor.description else []
    index = {name: i for i, name in enumerate(names)}
    return lambda values: Row(values, index)


_PLACEHOLDER = re.compile(r"\?")


def translate(sql: str) -> str:
    """Translate SQLite-flavoured SQL to PostgreSQL."""
    stripped = sql.strip()
    upper = stripped.upper()
    if upper.startswith("PRAGMA"):
        return ""
    if upper == "BEGIN IMMEDIATE":
        return ""
    if "%" in sql:
        sql = sql.replace("%", "%%")
    ignore = re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO", sql, re.I)
    if ignore:
        sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, count=1, flags=re.I)
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return _PLACEHOLDER.sub("%s", sql)


class _EmptyCursor:
    rowcount = 0

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list:
        return []

    def __iter__(self):
        return iter(())


class Connection:
    """Context-managed connection: commit on success, rollback on error."""

    def __init__(self, raw: Any, dialect: str) -> None:
        self.raw = raw
        self.dialect = dialect

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        if self.dialect == "sqlite":
            return self.raw.execute(sql, tuple(params))
        statement = translate(sql)
        if not statement:
            return _EmptyCursor()
        cursor = self.raw.cursor(row_factory=_pg_row_factory)
        cursor.execute(statement, tuple(params))
        return cursor

    def executescript(self, script: str) -> None:
        if self.dialect == "sqlite":
            self.raw.executescript(script)
            return
        for statement in (s.strip() for s in script.split(";")):
            if statement:
                self.raw.execute(statement)

    def columns(self, table: str) -> set[str]:
        if self.dialect == "sqlite":
            return {row["name"] for row in self.raw.execute(f"PRAGMA table_info({table})")}
        rows = self.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,))
        return {row[0] for row in rows}

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc_type is None:
                self.raw.commit()
            else:
                self.raw.rollback()
        finally:
            self.raw.close()


class Database:
    def __init__(self, url: str | Path) -> None:
        self.url = str(url)
        self.dialect = "postgres" if is_postgres(self.url) else "sqlite"
        if self.dialect == "postgres" and psycopg is None:
            raise RuntimeError("PostgreSQL needs psycopg: pip install -r requirements.txt")
        if self.dialect == "sqlite" and self.url != ":memory:":
            Path(self.url).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> Connection:
        if self.dialect == "postgres":
            raw = psycopg.connect(self.url, connect_timeout=10)
            return Connection(raw, "postgres")
        raw = sqlite3.connect(self.url, timeout=10)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("PRAGMA secure_delete = ON")
        raw.execute("PRAGMA busy_timeout = 10000")
        return Connection(raw, "sqlite")

    def ping(self) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1").fetchone()[0] == 1
