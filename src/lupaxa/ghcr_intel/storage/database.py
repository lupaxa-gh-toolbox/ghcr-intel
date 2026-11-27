"""SQLite cache of package documents and sync runs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3

__all__ = [
    "Database",
    "Document",
]

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS documents (
    package TEXT NOT NULL,
    category TEXT NOT NULL,
    body TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    error TEXT,
    PRIMARY KEY (package, category)
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    started_at REAL,
    finished_at REAL,
    stats_json TEXT
);
"""


@dataclass(frozen=True)
class Document:
    """One cached category for an ``owner/name`` package."""

    package: str
    category: str
    body: str
    fetched_at: float
    error: str | None


class Database:
    """
    Documents and sync runs stored in one SQLite file.

    ``package`` is ``owner/name``. Categories are ``metadata``, ``versions``,
    and ``manifest``.
    """

    def __init__(self, path: Path) -> None:
        """Create the file and the cache tables when they are missing."""

        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection:
            connection.executescript(_SCHEMA)

    def get(self, package: str, category: str) -> Document | None:
        """Return one document, or ``None`` when that category is absent."""

        with self._session() as connection:
            row = connection.execute(
                """
                SELECT package, category, body, fetched_at, error
                FROM documents
                WHERE package = ? AND category = ?
                """,
                (package, category),
            ).fetchone()
        if row is None:
            return None
        return Document(package=str(row[0]), category=str(row[1]), body=str(row[2]), fetched_at=float(row[3]), error=None if row[4] is None else str(row[4]))

    def put(self, package: str, category: str, *, body: str, fetched_at: float, error: str | None) -> None:
        """Insert or replace one document."""

        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO documents (package, category, body, fetched_at, error)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(package, category) DO UPDATE SET
                    body = excluded.body,
                    fetched_at = excluded.fetched_at,
                    error = excluded.error
                """,
                (package, category, body, fetched_at, error),
            )

    def info(self) -> dict[str, int]:
        """Return the file size and the document and sync-run counts."""

        with self._session() as connection:
            documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()
            runs = connection.execute("SELECT COUNT(*) FROM sync_runs").fetchone()
        return {
            "bytes": self.path.stat().st_size if self.path.is_file() else 0,
            "documents": 0 if documents is None else int(documents[0]),
            "sync_runs": 0 if runs is None else int(runs[0]),
        }

    def clear(self) -> None:
        """Delete every document and sync run. The file stays."""

        with self._session() as connection:
            connection.execute("DELETE FROM documents")
            connection.execute("DELETE FROM sync_runs")

    def vacuum(self) -> None:
        """Rebuild the SQLite file."""

        connection = sqlite3.connect(self.path)
        try:
            connection.isolation_level = None
            connection.execute("VACUUM")
        finally:
            connection.close()

    def latest_finished_at(self) -> float | None:
        """Return the newest ``sync_runs.finished_at``, or ``None``."""

        with self._session() as connection:
            row = connection.execute(
                """
                SELECT finished_at
                FROM sync_runs
                WHERE finished_at IS NOT NULL
                ORDER BY finished_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    def begin_run(self, started_at: float) -> int:
        """Insert a sync run and return its id."""

        with self._session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sync_runs (started_at, finished_at, stats_json)
                VALUES (?, NULL, NULL)
                """,
                (started_at,),
            )
            last = cursor.lastrowid
        if last is None:
            message = "The sync run was not recorded."
            raise RuntimeError(message)
        return int(last)

    def finish_run(self, run_id: int, finished_at: float, stats_json: str) -> None:
        """Record the finish time and the stats JSON for one run."""

        with self._session() as connection:
            connection.execute(
                """
                UPDATE sync_runs
                SET finished_at = ?, stats_json = ?
                WHERE id = ?
                """,
                (finished_at, stats_json, run_id),
            )

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
