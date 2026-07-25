"""Async SQLite persistence layer for RedirectHunter.

Four tables back every scan:

    - ``scan``    — one row per scan run (config snapshot, status, totals).
    - ``results`` — one row per validated candidate URL.
    - ``chain``   — one row per redirect hop (0..N-1) for a given result.
    - ``headers`` — the full response header set of the *final* hop of a
      result. Intermediate hop headers are intentionally **not** stored
      here in full: the fields that matter for an intermediate redirect
      hop (status code, ``Location``, ``Server``) are already captured on
      ``chain``, so duplicating every header of every intermediate 30x
      response would multiply storage several-fold for essentially no
      audit value. The terminal response — the page the operator actually
      lands on — is where the full header set (cookies, CSP, cache
      headers, etc.) is worth keeping.

All writes go through a single shared connection guarded by an
``asyncio.Lock``. SQLite serializes writers internally regardless of how
many coroutines attempt to write concurrently, so sharing one connection
under a lock is not a bottleneck relative to network I/O — a HEAD/GET
request takes orders of magnitude longer than an indexed insert — and it
avoids "database is locked" errors that plague multi-connection SQLite
access under concurrent writers.

Reads (``iter_results``) stream results in bounded batches rather than
loading a full 100k-row scan into memory, so ``export`` and ``show`` stay
memory-efficient at the scale the project requires.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import orjson

from redirecthunter.models import (
    FingerprintInfo,
    HTTPMethod,
    RedirectHop,
    RedirectResult,
    RedirectType,
    ScanConfig,
    ScanStatus,
    ScanSummary,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scan (
    scan_id       TEXT PRIMARY KEY,
    label         TEXT,
    input_path    TEXT NOT NULL,
    target        TEXT,
    status        TEXT NOT NULL,
    total_urls    INTEGER NOT NULL DEFAULT 0,
    config_json   TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);

CREATE TABLE IF NOT EXISTS results (
    result_id         TEXT PRIMARY KEY,
    scan_id           TEXT NOT NULL REFERENCES scan(scan_id) ON DELETE CASCADE,
    source_url        TEXT NOT NULL,
    expanded_url      TEXT NOT NULL,
    http_method       TEXT NOT NULL,
    status_code       INTEGER,
    redirect_type     TEXT NOT NULL,
    location          TEXT,
    final_url         TEXT,
    hop_count         INTEGER NOT NULL DEFAULT 0,
    server            TEXT,
    content_type      TEXT,
    content_length    INTEGER,
    cookies_json      TEXT NOT NULL DEFAULT '{}',
    fingerprint_json  TEXT NOT NULL DEFAULT '{}',
    alive             INTEGER NOT NULL,
    latency_ms        REAL NOT NULL,
    error             TEXT,
    timestamp         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_scan_id ON results(scan_id);
CREATE INDEX IF NOT EXISTS idx_results_scan_source ON results(scan_id, source_url);

CREATE TABLE IF NOT EXISTS chain (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id       TEXT NOT NULL REFERENCES results(result_id) ON DELETE CASCADE,
    hop_index       INTEGER NOT NULL,
    url             TEXT NOT NULL,
    status_code     INTEGER,
    redirect_type   TEXT NOT NULL,
    location_header TEXT,
    server_header   TEXT,
    latency_ms      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chain_result_id ON chain(result_id);

CREATE TABLE IF NOT EXISTS headers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id     TEXT NOT NULL REFERENCES results(result_id) ON DELETE CASCADE,
    header_name   TEXT NOT NULL,
    header_value  TEXT
);
CREATE INDEX IF NOT EXISTS idx_headers_result_id ON headers(result_id);
"""


class DatabaseError(Exception):
    """Raised on any unrecoverable SQLite persistence failure."""


def _row_to_hop(row: aiosqlite.Row) -> RedirectHop:
    """Deserialize one ``chain`` row into a RedirectHop."""
    return RedirectHop(
        hop_index=row["hop_index"],
        url=row["url"],
        status_code=row["status_code"],
        redirect_type=RedirectType(row["redirect_type"]),
        location_header=row["location_header"],
        server_header=row["server_header"],
        latency_ms=row["latency_ms"],
    )


def _row_to_result(row: aiosqlite.Row, hops: list[RedirectHop]) -> RedirectResult:
    """Deserialize one ``results`` row (plus its pre-fetched hops) into a RedirectResult."""
    fingerprint_data: dict[str, Any] = orjson.loads(row["fingerprint_json"] or "{}")
    cookies_data: dict[str, str] = orjson.loads(row["cookies_json"] or "{}")
    return RedirectResult(
        result_id=row["result_id"],
        scan_id=row["scan_id"],
        source_url=row["source_url"],
        expanded_url=row["expanded_url"],
        http_method=HTTPMethod(row["http_method"]),
        status_code=row["status_code"],
        redirect_type=RedirectType(row["redirect_type"]),
        location=row["location"],
        final_url=row["final_url"],
        redirect_chain=hops,
        hop_count=row["hop_count"],
        server=row["server"],
        content_type=row["content_type"],
        content_length=row["content_length"],
        cookies=cookies_data,
        fingerprint=FingerprintInfo.model_validate(fingerprint_data),
        alive=bool(row["alive"]),
        latency_ms=row["latency_ms"],
        error=row["error"],
        timestamp=row["timestamp"],
    )


class Database:
    """Async repository wrapping a single SQLite database file.

    Usage::

        async with Database(Path("scan.db")) as db:
            await db.create_scan(config, total_urls=1000)
            await db.save_result(result)
            summary = await db.get_scan_summary(config.scan_id)
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the database connection, enable WAL mode, and ensure schema exists."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        """Close the database connection, if open."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Database:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise DatabaseError("Database is not connected. Call connect() or use 'async with'.")
        return self._conn

    async def create_scan(self, config: ScanConfig, total_urls: int) -> None:
        """Insert a new scan record.

        Args:
            config: The fully-resolved scan configuration. Persisted as
                JSON so ``resume`` can rebuild an identical ``ScanConfig``
                without the operator re-specifying every flag.
            total_urls: Total number of candidate URLs discovered in the
                input file, used to compute progress percentage.
        """
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute(
                """
                INSERT INTO scan (scan_id, label, input_path, target, status, total_urls,
                                   config_json, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    config.scan_id,
                    config.scan_label,
                    str(config.input_path),
                    config.target,
                    ScanStatus.RUNNING.value,
                    total_urls,
                    config.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            await conn.commit()

    async def get_scan_config(self, scan_id: str) -> ScanConfig | None:
        """Fetch and rebuild the ``ScanConfig`` originally used for ``scan_id``.

        Used by the ``resume`` command to reconstruct the exact engine
        settings (workers, timeout, retries, headers, ...) of the original
        run without requiring the operator to retype every flag.
        """
        conn = self._require_conn()
        cursor = await conn.execute("SELECT config_json FROM scan WHERE scan_id = ?", (scan_id,))
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return ScanConfig.model_validate_json(row["config_json"])

    async def scan_exists(self, scan_id: str) -> bool:
        """Return True if a scan with this ID has been recorded."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT 1 FROM scan WHERE scan_id = ?", (scan_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def update_scan_status(
        self, scan_id: str, status: ScanStatus, *, finished: bool = False
    ) -> None:
        """Update a scan's lifecycle status, optionally stamping ``finished_at``."""
        conn = self._require_conn()
        async with self._write_lock:
            if finished:
                await conn.execute(
                    "UPDATE scan SET status = ?, finished_at = ? WHERE scan_id = ?",
                    (status.value, datetime.now(UTC).isoformat(), scan_id),
                )
            else:
                await conn.execute(
                    "UPDATE scan SET status = ? WHERE scan_id = ?", (status.value, scan_id)
                )
            await conn.commit()

    async def get_completed_source_urls(self, scan_id: str) -> set[str]:
        """Return the set of ``source_url`` values already recorded for a scan.

        Used by ``resume`` to filter the input file down to only the
        candidate URLs that have not yet been processed.
        """
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT source_url FROM results WHERE scan_id = ?", (scan_id,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {row["source_url"] for row in rows}

    async def save_result(
        self,
        result: RedirectResult,
        final_headers: Mapping[str, str] | None = None,
    ) -> None:
        """Persist one completed result, its redirect chain, and its final headers.

        All three tables are written in a single transaction so a crash
        mid-write can never leave an orphaned partial result behind.

        Args:
            result: The completed validation result.
            final_headers: The full header set of the terminal response,
                stored in the ``headers`` table. ``None`` for results with
                no successful terminal response (e.g. a connection error).
        """
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute(
                """
                INSERT INTO results (
                    result_id, scan_id, source_url, expanded_url, http_method, status_code,
                    redirect_type, location, final_url, hop_count, server, content_type,
                    content_length, cookies_json, fingerprint_json, alive, latency_ms,
                    error, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.result_id,
                    result.scan_id,
                    result.source_url,
                    result.expanded_url,
                    result.http_method.value,
                    result.status_code,
                    result.redirect_type.value,
                    result.location,
                    result.final_url,
                    result.hop_count,
                    result.server,
                    result.content_type,
                    result.content_length,
                    orjson.dumps(result.cookies).decode(),
                    orjson.dumps(result.fingerprint.model_dump(mode="json")).decode(),
                    int(result.alive),
                    result.latency_ms,
                    result.error,
                    result.timestamp.isoformat(),
                ),
            )

            if result.redirect_chain:
                await conn.executemany(
                    """
                    INSERT INTO chain (
                        result_id, hop_index, url, status_code, redirect_type,
                        location_header, server_header, latency_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            result.result_id,
                            hop.hop_index,
                            hop.url,
                            hop.status_code,
                            hop.redirect_type.value,
                            hop.location_header,
                            hop.server_header,
                            hop.latency_ms,
                        )
                        for hop in result.redirect_chain
                    ],
                )

            if final_headers:
                await conn.executemany(
                    "INSERT INTO headers (result_id, header_name, header_value) VALUES (?, ?, ?)",
                    [(result.result_id, str(name), str(value)) for name, value in final_headers.items()],
                )

            await conn.commit()

    async def get_scan_summary(self, scan_id: str) -> ScanSummary | None:
        """Compute aggregate statistics for a scan, for the ``stats`` command."""
        conn = self._require_conn()
        scan_cursor = await conn.execute(
            "SELECT label, status, total_urls, started_at, finished_at FROM scan WHERE scan_id = ?",
            (scan_id,),
        )
        scan_row = await scan_cursor.fetchone()
        await scan_cursor.close()
        if scan_row is None:
            return None

        agg_cursor = await conn.execute(
            """
            SELECT
                COUNT(*) AS completed,
                COALESCE(SUM(alive), 0) AS alive_count,
                COALESCE(SUM(CASE WHEN redirect_type != 'none' THEN 1 ELSE 0 END), 0) AS redirects,
                COALESCE(SUM(
                    CASE WHEN json_extract(fingerprint_json, '$.cloudflare.is_cloudflare') = 1
                         THEN 1 ELSE 0 END
                ), 0) AS cloudflare_count,
                COALESCE(AVG(latency_ms), 0.0) AS avg_latency
            FROM results WHERE scan_id = ?
            """,
            (scan_id,),
        )
        agg_row = await agg_cursor.fetchone()
        await agg_cursor.close()

        completed = agg_row["completed"]
        alive_count = agg_row["alive_count"]

        return ScanSummary(
            scan_id=scan_id,
            label=scan_row["label"],
            status=ScanStatus(scan_row["status"]),
            total_urls=scan_row["total_urls"],
            completed=completed,
            alive=alive_count,
            dead=completed - alive_count,
            redirects_found=agg_row["redirects"],
            cloudflare_protected=agg_row["cloudflare_count"],
            avg_latency_ms=round(agg_row["avg_latency"], 2),
            started_at=scan_row["started_at"],
            finished_at=scan_row["finished_at"],
        )

    async def list_scans(self) -> list[ScanSummary]:
        """Return summaries for every scan recorded in this database, newest first."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT scan_id FROM scan ORDER BY started_at DESC")
        rows = await cursor.fetchall()
        await cursor.close()
        summaries = []
        for row in rows:
            summary = await self.get_scan_summary(row["scan_id"])
            if summary is not None:
                summaries.append(summary)
        return summaries

    async def iter_results(
        self, scan_id: str, batch_size: int = 500
    ) -> AsyncIterator[RedirectResult]:
        """Stream all results for a scan in memory-bounded batches.

        Never loads a full scan's results into memory at once — at 100k
        URLs, buffering everything would work but defeats the point of a
        streaming exporter. Each batch fetches its own chain hops via a
        single ``IN (...)`` query rather than one query per result
        (avoiding N+1 query overhead), keeping total query count at
        roughly ``2 * ceil(total_results / batch_size)``.

        Args:
            scan_id: The scan to stream results for.
            batch_size: Number of result rows fetched per round-trip.

        Yields:
            Fully-reconstructed ``RedirectResult`` instances, ordered by
            completion timestamp.
        """
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM results WHERE scan_id = ? ORDER BY timestamp ASC", (scan_id,)
        )
        try:
            while True:
                rows = await cursor.fetchmany(batch_size)
                if not rows:
                    break

                result_ids = [row["result_id"] for row in rows]
                placeholders = ",".join("?" for _ in result_ids)
                chain_cursor = await conn.execute(
                    f"SELECT * FROM chain WHERE result_id IN ({placeholders}) "
                    f"ORDER BY result_id, hop_index",
                    result_ids,
                )
                chain_rows = await chain_cursor.fetchall()
                await chain_cursor.close()

                hops_by_result: dict[str, list[RedirectHop]] = defaultdict(list)
                for chain_row in chain_rows:
                    hops_by_result[chain_row["result_id"]].append(_row_to_hop(chain_row))

                for row in rows:
                    yield _row_to_result(row, hops_by_result.get(row["result_id"], []))
        finally:
            await cursor.close()

    async def export_scan_to_sqlite(self, scan_id: str, destination: Path) -> int:
        """Export one scan's full data (all four tables) to a new standalone SQLite file.

        Implemented as a pure-SQL copy via ``ATTACH DATABASE`` rather than
        looping over rows in Python — at 100k+ results this is both faster
        and simpler than reconstructing every ``RedirectResult`` just to
        re-insert it, and it preserves the ``headers`` table (which
        :meth:`iter_results` does not reconstruct, since headers are not
        part of the ``RedirectResult`` model).

        Args:
            scan_id: The scan to export.
            destination: Path to the new SQLite file. If a file already
                exists at this path, it is deleted first so the export
                starts from a clean schema.

        Returns:
            The number of result rows copied.

        Raises:
            DatabaseError: If the scan does not exist.
        """
        conn = self._require_conn()

        if not await self.scan_exists(scan_id):
            raise DatabaseError(f"No such scan: {scan_id}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        if await asyncio.to_thread(destination.exists):
            await asyncio.to_thread(destination.unlink)

        # The destination file needs its schema created independently first
        # (via a throwaway Database connection, then closed) so that the
        # subsequent ATTACH + INSERT...SELECT from the source connection
        # only ever has to perform DML, never schema-qualified DDL — which
        # is the fragile part of cross-database SQLite operations.
        dest_db = Database(destination)
        await dest_db.connect()
        await dest_db.close()

        async with self._write_lock:
            await conn.execute("ATTACH DATABASE ? AS export_db", (str(destination),))
            try:
                await conn.execute(
                    "INSERT INTO export_db.scan SELECT * FROM main.scan WHERE scan_id = ?",
                    (scan_id,),
                )
                await conn.execute(
                    "INSERT INTO export_db.results SELECT * FROM main.results WHERE scan_id = ?",
                    (scan_id,),
                )
                await conn.execute(
                    """
                    INSERT INTO export_db.chain
                    SELECT c.* FROM main.chain c
                    JOIN main.results r ON c.result_id = r.result_id
                    WHERE r.scan_id = ?
                    """,
                    (scan_id,),
                )
                await conn.execute(
                    """
                    INSERT INTO export_db.headers
                    SELECT h.* FROM main.headers h
                    JOIN main.results r ON h.result_id = r.result_id
                    WHERE r.scan_id = ?
                    """,
                    (scan_id,),
                )
                await conn.commit()

                count_cursor = await conn.execute("SELECT COUNT(*) AS n FROM export_db.results")
                count_row = await count_cursor.fetchone()
                await count_cursor.close()
            finally:
                await conn.execute("DETACH DATABASE export_db")

        return count_row["n"] if count_row else 0

    async def resolve_scan_id(self, partial_id: str) -> str:
        """Resolve a full or shortened (prefix) scan_id to its full UUID.

        Lets the CLI accept a short, git-style prefix (e.g. the first 8
        characters) instead of requiring the operator to type or paste a
        full UUID every time.

        Args:
            partial_id: A full scan_id, or an unambiguous prefix of one.

        Returns:
            The full scan_id.

        Raises:
            DatabaseError: If no scan matches, or if the prefix matches
                more than one scan (ambiguous).
        """
        if await self.scan_exists(partial_id):
            return partial_id

        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT scan_id FROM scan WHERE scan_id LIKE ? ESCAPE '\\'",
            (partial_id.replace("%", r"\%").replace("_", r"\_") + "%",),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        matches = [row["scan_id"] for row in rows]

        if not matches:
            raise DatabaseError(f"No scan found matching '{partial_id}'.")
        if len(matches) > 1:
            preview = ", ".join(m[:8] for m in matches[:5])
            suffix = ", ..." if len(matches) > 5 else ""
            raise DatabaseError(
                f"'{partial_id}' matches {len(matches)} scans ({preview}{suffix}). "
                f"Use more characters to disambiguate."
            )
        return matches[0]

    async def get_result_count(self, scan_id: str) -> int:
        """Return the number of results recorded so far for a scan."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM results WHERE scan_id = ?", (scan_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row["n"] if row else 0


__all__ = ["Database", "DatabaseError"]
