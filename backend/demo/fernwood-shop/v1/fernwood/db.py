"""Thin database wrapper around a DB-API connection."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any


class Database:
    """Executes parameterised SQL and manages transactions."""

    def __init__(self, connection: Any):
        self.connection = connection

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        cursor = self.connection.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall() if cursor.description else []

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> tuple[Any, ...] | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """Commit when the block succeeds, roll back when it raises."""
        try:
            yield self
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
