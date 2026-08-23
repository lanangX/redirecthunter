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
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import orjson

from redirecthunter.backlink import BacklinkResult
from redirecthunter.models import (
    BacklinkChainConfig,
    BacklinkChainSummary,
    BacklinkCheckConfig,
    BacklinkCheckSummary,
    CrawlConfig,
    CrawlLinkResult,
    CrawlPageResult,
    CrawlSeedMode,
    CrawlSummary,
    FingerprintInfo,
    HTTPMethod,
    LinkKind,
    PageIssue,
    RedirectHop,
    RedirectResult,
    RedirectType,
    RunStatus,
    ScanConfig,
    ScanSummary,
)
from redirecthunter.run import RunLifecycle, RunLifecycleError

#: Lifecycle descriptors for the run kinds migrated onto :mod:`redirecthunter.run`
#: so far -- ``crawl`` and ``backlink_check`` (see CONTEXT.md's "Run" entry for
#: why ``scan`` isn't migrated yet: its extra ``get_completed_source_urls``/
#: ``export_scan_to_sqlite`` methods need their own follow-up decision first).
_CRAWL_LIFECYCLE: RunLifecycle[CrawlConfig] = RunLifecycle(
    table="crawls",
    id_column="crawl_id",
    config_model=CrawlConfig,
    get_id=lambda c: c.crawl_id,
    get_label=lambda c: c.crawl_label,
    kind_label="crawl",
    extra_columns=("seed_mode", "seed_url", "seed_input_path"),
    extract_extra=lambda c: (
        c.seed_mode.value,
        c.seed_url,
        str(c.seed_input_path) if c.seed_input_path else None,
    ),
)

_BACKLINK_LIFECYCLE: RunLifecycle[BacklinkCheckConfig] = RunLifecycle(
    table="backlink_checks",
    id_column="backlink_id",
    config_model=BacklinkCheckConfig,
    get_id=lambda c: c.backlink_id,
    get_label=lambda c: c.label,
    kind_label="backlink check",
    extra_columns=("domain", "input_path"),
    extract_extra=lambda c: (c.domain, str(c.input_path)),
)

_BACKLINK_CHAIN_LIFECYCLE: RunLifecycle[BacklinkChainConfig] = RunLifecycle(
    table="backlink_chains",
    id_column="chain_id",
    config_model=BacklinkChainConfig,
    get_id=lambda c: c.chain_id,
    get_label=lambda c: c.label,
    kind_label="backlink chain",
    extra_columns=("domain",),
    extract_extra=lambda c: (c.domain,),
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
    body_link         TEXT,
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

-- Crawl-mode tables: the site-crawler counterpart to scan/results/chain
-- above. Kept as their own three tables (not folded into scan/results)
-- because a crawl's unit of work is a *page* (with on-page SEO signals
-- that a redirect-validation result has no fields for) plus a separate,
-- many-per-page *link* record -- a different shape from one row per
-- candidate URL. See docs/DATABASE_SCHEMA.md for the full reference.
CREATE TABLE IF NOT EXISTS crawls (
    crawl_id          TEXT PRIMARY KEY,
    label             TEXT,
    seed_mode         TEXT NOT NULL,
    seed_url          TEXT,
    seed_input_path   TEXT,
    status            TEXT NOT NULL,
    config_json       TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT
);

CREATE TABLE IF NOT EXISTS crawl_pages (
    page_id                    TEXT PRIMARY KEY,
    crawl_id                   TEXT NOT NULL REFERENCES crawls(crawl_id) ON DELETE CASCADE,
    url                        TEXT NOT NULL,
    depth                      INTEGER NOT NULL,
    discovered_from            TEXT,
    status_code                INTEGER,
    alive                      INTEGER NOT NULL,
    redirected                 INTEGER NOT NULL DEFAULT 0,
    final_url                  TEXT,
    content_type               TEXT,
    title                      TEXT,
    title_length               INTEGER NOT NULL DEFAULT 0,
    meta_description           TEXT,
    meta_description_length    INTEGER NOT NULL DEFAULT 0,
    h1_json                    TEXT NOT NULL DEFAULT '[]',
    h1_count                   INTEGER NOT NULL DEFAULT 0,
    internal_link_count        INTEGER NOT NULL DEFAULT 0,
    external_link_count        INTEGER NOT NULL DEFAULT 0,
    word_count                 INTEGER NOT NULL DEFAULT 0,
    issues_json                TEXT NOT NULL DEFAULT '[]',
    latency_ms                 REAL NOT NULL,
    error                      TEXT,
    timestamp                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_pages_crawl_id ON crawl_pages(crawl_id);
CREATE INDEX IF NOT EXISTS idx_crawl_pages_crawl_url ON crawl_pages(crawl_id, url);

CREATE TABLE IF NOT EXISTS crawl_links (
    link_id           TEXT PRIMARY KEY,
    crawl_id          TEXT NOT NULL REFERENCES crawls(crawl_id) ON DELETE CASCADE,
    source_page_url   TEXT NOT NULL,
    target_url        TEXT NOT NULL,
    raw_href          TEXT NOT NULL,
    link_kind         TEXT NOT NULL,
    anchor_text       TEXT,
    rel               TEXT,
    target_attr       TEXT,
    status_code       INTEGER,
    is_broken         INTEGER NOT NULL,
    redirected        INTEGER NOT NULL DEFAULT 0,
    final_url         TEXT,
    error             TEXT,
    latency_ms        REAL NOT NULL DEFAULT 0,
    checked_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_links_crawl_id ON crawl_links(crawl_id);
CREATE INDEX IF NOT EXISTS idx_crawl_links_broken ON crawl_links(crawl_id, is_broken);
CREATE INDEX IF NOT EXISTS idx_crawl_links_source ON crawl_links(crawl_id, source_page_url);

-- Backlink-check tables: persistence for `bl-check`, mirroring the
-- crawls/crawl_pages shape ("one run" + "one row per thing checked").
-- Shares the matching logic in redirecthunter/backlink.py with the
-- standalone backlink_checker.py/backlink_checker_js.py scripts -- see
-- MEMORY.md for why those scripts still live at the repo root instead of
-- being folded into this table set.
CREATE TABLE IF NOT EXISTS backlink_checks (
    backlink_id       TEXT PRIMARY KEY,
    label             TEXT,
    domain            TEXT NOT NULL,
    input_path        TEXT NOT NULL,
    status            TEXT NOT NULL,
    config_json       TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT
);

CREATE TABLE IF NOT EXISTS backlink_results (
    result_id         TEXT PRIMARY KEY,
    backlink_id       TEXT NOT NULL REFERENCES backlink_checks(backlink_id) ON DELETE CASCADE,
    source_url        TEXT NOT NULL,
    final_url         TEXT,
    status_code       INTEGER,
    match_found       INTEGER NOT NULL DEFAULT 0,
    match_type        TEXT NOT NULL DEFAULT 'not_found',
    matched_href      TEXT,
    rel               TEXT,
    target            TEXT,
    matched_target    TEXT,
    blocked           INTEGER NOT NULL DEFAULT 0,
    requires_login    INTEGER NOT NULL DEFAULT 0,
    text_mentions     INTEGER NOT NULL DEFAULT 0,
    robots_meta       TEXT,
    robots_header     TEXT,
    notes             TEXT,
    error             TEXT,
    checked_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlink_results_backlink_id ON backlink_results(backlink_id);
CREATE INDEX IF NOT EXISTS idx_backlink_results_match ON backlink_results(backlink_id, match_found);

-- Backlink-chain tables: `bl-chain`'s tiered-verification counterpart to
-- backlink_checks/backlink_results above. `backlink_chains` mirrors
-- `backlink_checks` (one row per chain run). `backlink_chain_tiers` is a
-- thin join/ordering table, deliberately NOT a `chain_id` column added
-- directly onto `backlink_checks` -- a `backlink_checks` row keeps
-- meaning exactly what it means today (one ordinary, standalone
-- `bl-check` run) whether or not it happens to also be one tier of a
-- chain. `ON DELETE CASCADE` only goes from `backlink_chains` to
-- `backlink_chain_tiers`: deleting a chain never deletes the underlying
-- `backlink_checks`/`backlink_results` rows it points at (no CASCADE is
-- declared on `backlink_id`'s REFERENCES for that reason -- those rows
-- remain independently valid, addressable `bl-check` runs).
CREATE TABLE IF NOT EXISTS backlink_chains (
    chain_id      TEXT PRIMARY KEY,
    label         TEXT,
    domain        TEXT NOT NULL,
    status        TEXT NOT NULL,
    config_json   TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);

CREATE TABLE IF NOT EXISTS backlink_chain_tiers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id      TEXT NOT NULL REFERENCES backlink_chains(chain_id) ON DELETE CASCADE,
    tier_index    INTEGER NOT NULL,
    backlink_id   TEXT NOT NULL REFERENCES backlink_checks(backlink_id),
    input_path    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlink_chain_tiers_chain_id ON backlink_chain_tiers(chain_id);
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
        body_link=row["body_link"],
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


def _row_to_crawl_page(row: aiosqlite.Row) -> CrawlPageResult:
    """Deserialize one ``crawl_pages`` row into a CrawlPageResult."""
    h1_texts: list[str] = orjson.loads(row["h1_json"] or "[]")
    issue_values: list[str] = orjson.loads(row["issues_json"] or "[]")
    return CrawlPageResult(
        page_id=row["page_id"],
        crawl_id=row["crawl_id"],
        url=row["url"],
        depth=row["depth"],
        discovered_from=row["discovered_from"],
        status_code=row["status_code"],
        alive=bool(row["alive"]),
        redirected=bool(row["redirected"]),
        final_url=row["final_url"],
        content_type=row["content_type"],
        title=row["title"],
        title_length=row["title_length"],
        meta_description=row["meta_description"],
        meta_description_length=row["meta_description_length"],
        h1_texts=h1_texts,
        h1_count=row["h1_count"],
        internal_link_count=row["internal_link_count"],
        external_link_count=row["external_link_count"],
        word_count=row["word_count"],
        issues=[PageIssue(v) for v in issue_values],
        latency_ms=row["latency_ms"],
        error=row["error"],
        timestamp=row["timestamp"],
    )


def _row_to_crawl_link(row: aiosqlite.Row) -> CrawlLinkResult:
    """Deserialize one ``crawl_links`` row into a CrawlLinkResult."""
    return CrawlLinkResult(
        link_id=row["link_id"],
        crawl_id=row["crawl_id"],
        source_page_url=row["source_page_url"],
        target_url=row["target_url"],
        raw_href=row["raw_href"],
        link_kind=LinkKind(row["link_kind"]),
        anchor_text=row["anchor_text"],
        rel=row["rel"],
        target_attr=row["target_attr"],
        status_code=row["status_code"],
        is_broken=bool(row["is_broken"]),
        redirected=bool(row["redirected"]),
        final_url=row["final_url"],
        error=row["error"],
        latency_ms=row["latency_ms"],
        checked_at=row["checked_at"],
    )


def _row_to_backlink_result(row: aiosqlite.Row) -> BacklinkResult:
    """Deserialize one ``backlink_results`` row into a BacklinkResult."""
    notes_raw = row["notes"] or ""
    notes = notes_raw.split(" | ") if notes_raw else []
    return BacklinkResult(
        source_url=row["source_url"],
        final_url=row["final_url"],
        status_code=row["status_code"],
        match_found=bool(row["match_found"]),
        match_type=row["match_type"],
        matched_href=row["matched_href"],
        rel=row["rel"],
        target=row["target"],
        matched_target=row["matched_target"],
        blocked=bool(row["blocked"]),
        requires_login=bool(row["requires_login"]),
        error=row["error"],
        text_mentions=row["text_mentions"],
        robots_meta=row["robots_meta"],
        robots_header=row["robots_header"],
        notes=notes,
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
        # Migration: idx_results_scan_id was dropped from the schema above because
        # idx_results_scan_source(scan_id, source_url) already serves scan_id-only
        # queries via its leading column, making the single-column index pure
        # duplicate storage. Existing databases created by older versions still
        # have it on disk, so remove it here rather than leaving it to linger.
        await self._conn.execute("DROP INDEX IF EXISTS idx_results_scan_id;")
        # Migration: `body_link` was added after the initial schema, so
        # databases created by older versions need it backfilled via
        # ALTER TABLE. SQLite has no "ADD COLUMN IF NOT EXISTS", so check
        # pragma table_info first -- re-running ALTER TABLE on a column
        # that already exists raises "duplicate column name".
        cursor = await self._conn.execute("PRAGMA table_info(results);")
        existing_columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if "body_link" not in existing_columns:
            await self._conn.execute("ALTER TABLE results ADD COLUMN body_link TEXT;")
        # Migration: `rel` / `target_attr` were added to `crawl_links` after
        # the initial schema, same reasoning as `body_link` above.
        cursor = await self._conn.execute("PRAGMA table_info(crawl_links);")
        existing_crawl_link_columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if "rel" not in existing_crawl_link_columns:
            await self._conn.execute("ALTER TABLE crawl_links ADD COLUMN rel TEXT;")
        if "target_attr" not in existing_crawl_link_columns:
            await self._conn.execute("ALTER TABLE crawl_links ADD COLUMN target_attr TEXT;")
        # Migration: `robots_meta` / `robots_header` were added to
        # `backlink_results` after the initial schema, same reasoning as
        # `body_link` above.
        cursor = await self._conn.execute("PRAGMA table_info(backlink_results);")
        existing_backlink_result_columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if "robots_meta" not in existing_backlink_result_columns:
            await self._conn.execute("ALTER TABLE backlink_results ADD COLUMN robots_meta TEXT;")
        if "robots_header" not in existing_backlink_result_columns:
            await self._conn.execute("ALTER TABLE backlink_results ADD COLUMN robots_header TEXT;")
        # Migration: `matched_target` was added to `backlink_results` after
        # the initial schema, same reasoning as `robots_meta`/`robots_header`
        # above -- it reuses the same `existing_backlink_result_columns`
        # PRAGMA read rather than issuing a second query for it.
        if "matched_target" not in existing_backlink_result_columns:
            await self._conn.execute("ALTER TABLE backlink_results ADD COLUMN matched_target TEXT;")
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
                    RunStatus.RUNNING.value,
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
        self, scan_id: str, status: RunStatus, *, finished: bool = False
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
                    redirect_type, location, final_url, body_link, hop_count, server, content_type,
                    content_length, cookies_json, fingerprint_json, alive, latency_ms,
                    error, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    result.body_link,
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
        assert agg_row is not None  # aggregate query with no GROUP BY always returns exactly one row

        completed = agg_row["completed"]
        alive_count = agg_row["alive_count"]

        return ScanSummary(
            scan_id=scan_id,
            label=scan_row["label"],
            status=RunStatus(scan_row["status"]),
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
    ) -> AsyncGenerator[RedirectResult]:
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

    # -- Crawl-mode persistence -------------------------------------------
    #
    # Mirrors the scan/results CRUD above one-for-one (create -> save rows
    # as they complete -> summarize -> stream for export), just against
    # the crawls/crawl_pages/crawl_links tables instead. Kept as its own
    # section rather than interleaved method-by-method with the scan
    # methods so the two lifecycles (each with their own status/summary
    # types) stay easy to read as complete, independent stories.

    async def create_crawl(self, config: CrawlConfig) -> None:
        """Insert a new crawl record."""
        conn = self._require_conn()
        async with self._write_lock:
            await _CRAWL_LIFECYCLE.create(conn, config)

    async def crawl_exists(self, crawl_id: str) -> bool:
        """Return True if a crawl with this ID has been recorded."""
        return await _CRAWL_LIFECYCLE.exists(self._require_conn(), crawl_id)

    async def get_crawl_config(self, crawl_id: str) -> CrawlConfig | None:
        """Fetch and rebuild the ``CrawlConfig`` originally used for ``crawl_id``."""
        return await _CRAWL_LIFECYCLE.get_config(self._require_conn(), crawl_id)

    async def update_crawl_status(
        self, crawl_id: str, status: RunStatus, *, finished: bool = False
    ) -> None:
        """Update a crawl's lifecycle status, optionally stamping ``finished_at``."""
        conn = self._require_conn()
        async with self._write_lock:
            await _CRAWL_LIFECYCLE.update_status(conn, crawl_id, status, finished=finished)

    async def save_crawl_page(self, page: CrawlPageResult) -> None:
        """Persist one fetched-and-analyzed page."""
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute(
                """
                INSERT INTO crawl_pages (
                    page_id, crawl_id, url, depth, discovered_from, status_code, alive,
                    redirected, final_url, content_type, title, title_length,
                    meta_description, meta_description_length, h1_json, h1_count,
                    internal_link_count, external_link_count, word_count, issues_json,
                    latency_ms, error, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page.page_id,
                    page.crawl_id,
                    page.url,
                    page.depth,
                    page.discovered_from,
                    page.status_code,
                    int(page.alive),
                    int(page.redirected),
                    page.final_url,
                    page.content_type,
                    page.title,
                    page.title_length,
                    page.meta_description,
                    page.meta_description_length,
                    orjson.dumps(page.h1_texts).decode(),
                    page.h1_count,
                    page.internal_link_count,
                    page.external_link_count,
                    page.word_count,
                    orjson.dumps([issue.value for issue in page.issues]).decode(),
                    page.latency_ms,
                    page.error,
                    page.timestamp.isoformat(),
                ),
            )
            await conn.commit()

    async def save_crawl_link(self, link: CrawlLinkResult) -> None:
        """Persist one checked link occurrence."""
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute(
                """
                INSERT INTO crawl_links (
                    link_id, crawl_id, source_page_url, target_url, raw_href, link_kind,
                    anchor_text, rel, target_attr, status_code, is_broken, redirected, final_url, error,
                    latency_ms, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link.link_id,
                    link.crawl_id,
                    link.source_page_url,
                    link.target_url,
                    link.raw_href,
                    link.link_kind.value,
                    link.anchor_text,
                    link.rel,
                    link.target_attr,
                    link.status_code,
                    int(link.is_broken),
                    int(link.redirected),
                    link.final_url,
                    link.error,
                    link.latency_ms,
                    link.checked_at.isoformat(),
                ),
            )
            await conn.commit()

    async def get_crawl_summary(self, crawl_id: str) -> CrawlSummary | None:
        """Compute aggregate statistics for a crawl, for the ``crawl-stats`` command.

        Duplicate-title and duplicate-meta-description counts are
        deliberately computed here via ``GROUP BY`` over every page's
        stored value, rather than being tracked incrementally during the
        crawl -- "is this title a duplicate" is a whole-crawl question a
        single in-flight page has no way to answer for itself while pages
        are still being discovered and fetched concurrently.
        """
        conn = self._require_conn()
        crawl_cursor = await conn.execute(
            "SELECT label, status, seed_mode, started_at, finished_at FROM crawls WHERE crawl_id = ?",
            (crawl_id,),
        )
        crawl_row = await crawl_cursor.fetchone()
        await crawl_cursor.close()
        if crawl_row is None:
            return None

        page_cursor = await conn.execute(
            """
            SELECT
                COUNT(*) AS pages_crawled,
                COALESCE(SUM(alive), 0) AS pages_alive,
                COALESCE(SUM(CASE WHEN title IS NULL OR title = '' THEN 1 ELSE 0 END), 0) AS missing_title,
                COALESCE(SUM(
                    CASE WHEN meta_description IS NULL OR meta_description = '' THEN 1 ELSE 0 END
                ), 0) AS missing_meta,
                COALESCE(SUM(CASE WHEN h1_count = 0 THEN 1 ELSE 0 END), 0) AS missing_h1,
                COALESCE(SUM(CASE WHEN h1_count > 1 THEN 1 ELSE 0 END), 0) AS multiple_h1,
                COALESCE(AVG(latency_ms), 0.0) AS avg_latency
            FROM crawl_pages WHERE crawl_id = ?
            """,
            (crawl_id,),
        )
        page_row = await page_cursor.fetchone()
        await page_cursor.close()
        assert page_row is not None  # aggregate query with no GROUP BY always returns exactly one row

        dup_title_cursor = await conn.execute(
            """
            SELECT COALESCE(SUM(n), 0) AS dup_count FROM (
                SELECT COUNT(*) AS n FROM crawl_pages
                WHERE crawl_id = ? AND title IS NOT NULL AND title != ''
                GROUP BY title HAVING COUNT(*) > 1
            )
            """,
            (crawl_id,),
        )
        dup_title_row = await dup_title_cursor.fetchone()
        await dup_title_cursor.close()

        dup_meta_cursor = await conn.execute(
            """
            SELECT COALESCE(SUM(n), 0) AS dup_count FROM (
                SELECT COUNT(*) AS n FROM crawl_pages
                WHERE crawl_id = ? AND meta_description IS NOT NULL AND meta_description != ''
                GROUP BY meta_description HAVING COUNT(*) > 1
            )
            """,
            (crawl_id,),
        )
        dup_meta_row = await dup_meta_cursor.fetchone()
        await dup_meta_cursor.close()

        link_cursor = await conn.execute(
            """
            SELECT
                COUNT(*) AS links_checked,
                COALESCE(SUM(is_broken), 0) AS broken_links,
                COALESCE(SUM(CASE WHEN link_kind = 'internal' AND is_broken THEN 1 ELSE 0 END), 0)
                    AS broken_internal,
                COALESCE(SUM(CASE WHEN link_kind = 'external' AND is_broken THEN 1 ELSE 0 END), 0)
                    AS broken_external
            FROM crawl_links WHERE crawl_id = ?
            """,
            (crawl_id,),
        )
        link_row = await link_cursor.fetchone()
        await link_cursor.close()
        assert link_row is not None

        pages_crawled = page_row["pages_crawled"]
        pages_alive = page_row["pages_alive"]

        return CrawlSummary(
            crawl_id=crawl_id,
            label=crawl_row["label"],
            status=RunStatus(crawl_row["status"]),
            seed_mode=CrawlSeedMode(crawl_row["seed_mode"]),
            pages_crawled=pages_crawled,
            pages_alive=pages_alive,
            pages_dead=pages_crawled - pages_alive,
            links_checked=link_row["links_checked"],
            broken_links=link_row["broken_links"],
            broken_internal_links=link_row["broken_internal"],
            broken_external_links=link_row["broken_external"],
            pages_missing_title=page_row["missing_title"],
            pages_duplicate_title=dup_title_row["dup_count"] if dup_title_row else 0,
            pages_missing_meta_description=page_row["missing_meta"],
            pages_duplicate_meta_description=dup_meta_row["dup_count"] if dup_meta_row else 0,
            pages_missing_h1=page_row["missing_h1"],
            pages_multiple_h1=page_row["multiple_h1"],
            avg_latency_ms=round(page_row["avg_latency"], 2),
            started_at=crawl_row["started_at"],
            finished_at=crawl_row["finished_at"],
        )

    async def list_crawls(self) -> list[CrawlSummary]:
        """Return summaries for every crawl recorded in this database, newest first."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT crawl_id FROM crawls ORDER BY started_at DESC")
        rows = await cursor.fetchall()
        await cursor.close()
        summaries = []
        for row in rows:
            summary = await self.get_crawl_summary(row["crawl_id"])
            if summary is not None:
                summaries.append(summary)
        return summaries

    async def iter_crawl_pages(
        self, crawl_id: str, *, broken_links_only: bool = False, batch_size: int = 500
    ) -> AsyncGenerator[CrawlPageResult]:
        """Stream a crawl's pages in memory-bounded batches, ordered by fetch time.

        Args:
            crawl_id: The crawl to stream pages for.
            broken_links_only: If True, only yield pages that link to
                something broken -- either an occurrence recorded in
                ``crawl_links`` (``is_broken = 1``), or an internal link
                that was itself promoted straight to a page fetch (see
                ``Crawler._enqueue_discovered_link``) and turned out dead
                (no response, or a 4xx/5xx status), matched via that
                page's ``discovered_from`` column. Answered with two
                ``EXISTS`` subqueries rather than a stored per-page flag,
                since a page's outlinks/discoveries can finish (or arrive)
                after the page itself was already persisted -- see
                ``CrawlPageResult.issues``'s docstring.
            batch_size: Number of rows fetched per round-trip.
        """
        conn = self._require_conn()
        query = "SELECT * FROM crawl_pages WHERE crawl_id = ?"
        if broken_links_only:
            query += """ AND (
                EXISTS (
                    SELECT 1 FROM crawl_links cl
                    WHERE cl.crawl_id = crawl_pages.crawl_id
                      AND cl.source_page_url = crawl_pages.url
                      AND cl.is_broken = 1
                )
                OR EXISTS (
                    SELECT 1 FROM crawl_pages dead
                    WHERE dead.crawl_id = crawl_pages.crawl_id
                      AND dead.discovered_from = crawl_pages.url
                      AND (dead.alive = 0 OR dead.status_code >= 400)
                )
            )"""
        query += " ORDER BY timestamp ASC"

        cursor = await conn.execute(query, (crawl_id,))
        try:
            while True:
                rows = await cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    yield _row_to_crawl_page(row)
        finally:
            await cursor.close()

    async def iter_crawl_links(
        self, crawl_id: str, *, broken_only: bool = False, batch_size: int = 500
    ) -> AsyncGenerator[CrawlLinkResult]:
        """Stream a crawl's checked links in memory-bounded batches."""
        conn = self._require_conn()
        query = "SELECT * FROM crawl_links WHERE crawl_id = ?"
        if broken_only:
            query += " AND is_broken = 1"
        query += " ORDER BY checked_at ASC"

        cursor = await conn.execute(query, (crawl_id,))
        try:
            while True:
                rows = await cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    yield _row_to_crawl_link(row)
        finally:
            await cursor.close()

    async def resolve_crawl_id(self, partial_id: str) -> str:
        """Resolve a full or shortened (prefix) crawl_id to its full UUID. Mirrors ``resolve_scan_id``."""
        try:
            return await _CRAWL_LIFECYCLE.resolve_id(self._require_conn(), partial_id)
        except RunLifecycleError as exc:
            raise DatabaseError(str(exc)) from exc

    async def get_crawl_page_count(self, crawl_id: str) -> int:
        """Return the number of pages recorded so far for a crawl."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM crawl_pages WHERE crawl_id = ?", (crawl_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row["n"] if row else 0

    async def delete_crawl(self, crawl_id: str) -> int:
        """Permanently delete a crawl and every page/link row that belongs to it.

        Relies on ``ON DELETE CASCADE`` the same way :meth:`delete_scan` does.
        """
        conn = self._require_conn()

        if not await self.crawl_exists(crawl_id):
            raise DatabaseError(f"No such crawl: {crawl_id}")

        page_count = await self.get_crawl_page_count(crawl_id)

        async with self._write_lock:
            await _CRAWL_LIFECYCLE.delete(conn, crawl_id)

        return page_count

    # ----------------------------------------------------------------
    # Backlink-check persistence -- mirrors the crawl* methods above.
    # ----------------------------------------------------------------

    async def create_backlink_check(self, config: BacklinkCheckConfig, total_urls: int) -> None:
        """Insert a new backlink-check record."""
        conn = self._require_conn()
        async with self._write_lock:
            await _BACKLINK_LIFECYCLE.create(conn, config)
        del total_urls  # not stored as its own column; recoverable via COUNT(*) on backlink_results

    async def backlink_check_exists(self, backlink_id: str) -> bool:
        """Return True if a backlink-check run with this ID has been recorded."""
        return await _BACKLINK_LIFECYCLE.exists(self._require_conn(), backlink_id)

    async def get_backlink_check_config(self, backlink_id: str) -> BacklinkCheckConfig | None:
        """Fetch and rebuild the ``BacklinkCheckConfig`` originally used for ``backlink_id``."""
        return await _BACKLINK_LIFECYCLE.get_config(self._require_conn(), backlink_id)

    async def update_backlink_check_status(
        self, backlink_id: str, status: RunStatus, *, finished: bool = False
    ) -> None:
        """Update a backlink-check run's lifecycle status, optionally stamping ``finished_at``."""
        conn = self._require_conn()
        async with self._write_lock:
            await _BACKLINK_LIFECYCLE.update_status(conn, backlink_id, status, finished=finished)

    async def save_backlink_result(self, backlink_id: str, result: BacklinkResult) -> None:
        """Persist one checked URL's result.

        ``BacklinkResult`` itself carries no ``backlink_id``/``result_id``/
        ``checked_at`` (it's shared with the standalone root scripts, which
        have no database concept at all -- see ``redirecthunter/backlink.py``),
        so this method generates those the same way ``save_crawl_page`` etc.
        generate primary keys and timestamps on the way into the database.
        """
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute(
                """
                INSERT INTO backlink_results (
                    result_id, backlink_id, source_url, final_url, status_code,
                    match_found, match_type, matched_href, rel, target, matched_target,
                    blocked, requires_login, text_mentions, robots_meta, robots_header,
                    notes, error, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    backlink_id,
                    result.source_url,
                    result.final_url,
                    result.status_code,
                    int(result.match_found),
                    result.match_type,
                    result.matched_href,
                    result.rel,
                    result.target,
                    result.matched_target,
                    int(result.blocked),
                    int(result.requires_login),
                    result.text_mentions,
                    result.robots_meta,
                    result.robots_header,
                    " | ".join(result.notes),
                    result.error,
                    datetime.now(UTC).isoformat(),
                ),
            )
            await conn.commit()

    async def get_backlink_check_summary(self, backlink_id: str) -> BacklinkCheckSummary | None:
        """Compute aggregate statistics for a backlink-check run, for ``bl-stats``."""
        conn = self._require_conn()
        check_cursor = await conn.execute(
            "SELECT label, domain, status, started_at, finished_at FROM backlink_checks WHERE backlink_id = ?",
            (backlink_id,),
        )
        check_row = await check_cursor.fetchone()
        await check_cursor.close()
        if check_row is None:
            return None

        result_cursor = await conn.execute(
            """
            SELECT
                COUNT(*) AS total_urls,
                COALESCE(SUM(CASE WHEN match_type IN ('anchor', 'subdomain_anchor', 'final_url_is_target')
                    THEN 1 ELSE 0 END), 0) AS confirmed,
                COALESCE(SUM(CASE WHEN match_type = 'indirect_query' THEN 1 ELSE 0 END), 0) AS indirect,
                COALESCE(SUM(CASE WHEN match_type = 'text_mention_only' THEN 1 ELSE 0 END), 0) AS text_mention_only,
                COALESCE(SUM(CASE WHEN match_type = 'not_found' AND blocked = 0 AND requires_login = 0
                    AND error IS NULL THEN 1 ELSE 0 END), 0) AS not_found,
                COALESCE(SUM(blocked), 0) AS blocked,
                COALESCE(SUM(requires_login), 0) AS requires_login,
                COALESCE(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END), 0) AS error
            FROM backlink_results WHERE backlink_id = ?
            """,
            (backlink_id,),
        )
        result_row = await result_cursor.fetchone()
        await result_cursor.close()
        assert result_row is not None  # aggregate query with no GROUP BY always returns exactly one row

        return BacklinkCheckSummary(
            backlink_id=backlink_id,
            domain=check_row["domain"],
            label=check_row["label"],
            status=RunStatus(check_row["status"]),
            total_urls=result_row["total_urls"],
            confirmed=result_row["confirmed"],
            indirect=result_row["indirect"],
            text_mention_only=result_row["text_mention_only"],
            not_found=result_row["not_found"],
            blocked=result_row["blocked"],
            requires_login=result_row["requires_login"],
            error=result_row["error"],
            started_at=check_row["started_at"],
            finished_at=check_row["finished_at"],
        )

    async def list_backlink_checks(self) -> list[BacklinkCheckSummary]:
        """Return summaries for every backlink-check run recorded in this database, newest first."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT backlink_id FROM backlink_checks ORDER BY started_at DESC")
        rows = await cursor.fetchall()
        await cursor.close()
        summaries = []
        for row in rows:
            summary = await self.get_backlink_check_summary(row["backlink_id"])
            if summary is not None:
                summaries.append(summary)
        return summaries

    async def iter_backlink_results(
        self,
        backlink_id: str,
        *,
        confirmed_only: bool = False,
        match_type: str | None = None,
        batch_size: int = 500,
    ) -> AsyncGenerator[BacklinkResult]:
        """Stream a backlink-check run's per-URL results in memory-bounded batches.

        Every call site must wrap this in ``contextlib.aclosing(...)`` if it
        might ``break`` out of the loop early (e.g. a ``--limit``) -- see
        MEMORY.md's note on the ``crawl-show`` fix this convention comes
        from; a bare ``async for`` that breaks early leaves this generator's
        ``finally: await cursor.close()`` to run during GC, potentially
        after the caller's own ``finally: await db.close()`` has already
        closed the connection.
        """
        conn = self._require_conn()
        query = "SELECT * FROM backlink_results WHERE backlink_id = ?"
        params: list[Any] = [backlink_id]
        if confirmed_only:
            query += " AND match_found = 1"
        if match_type is not None:
            query += " AND match_type = ?"
            params.append(match_type)
        query += " ORDER BY checked_at ASC"

        cursor = await conn.execute(query, tuple(params))
        try:
            while True:
                rows = await cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    yield _row_to_backlink_result(row)
        finally:
            await cursor.close()

    async def resolve_backlink_check_id(self, partial_id: str) -> str:
        """Resolve a full or shortened (prefix) backlink_id to its full UUID. Mirrors ``resolve_crawl_id``."""
        try:
            return await _BACKLINK_LIFECYCLE.resolve_id(self._require_conn(), partial_id)
        except RunLifecycleError as exc:
            raise DatabaseError(str(exc)) from exc

    async def get_backlink_result_count(self, backlink_id: str) -> int:
        """Return the number of results recorded so far for a backlink-check run."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM backlink_results WHERE backlink_id = ?", (backlink_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row["n"] if row else 0

    async def delete_backlink_check(self, backlink_id: str) -> int:
        """Permanently delete a backlink-check run and every result row that belongs to it.

        Relies on ``ON DELETE CASCADE`` the same way :meth:`delete_crawl` does.
        """
        conn = self._require_conn()

        if not await self.backlink_check_exists(backlink_id):
            raise DatabaseError(f"No such backlink check: {backlink_id}")

        result_count = await self.get_backlink_result_count(backlink_id)

        async with self._write_lock:
            await _BACKLINK_LIFECYCLE.delete(conn, backlink_id)

        return result_count

    # ----------------------------------------------------------------
    # Backlink-chain persistence -- `bl-chain`'s tiered-verification
    # counterpart to the backlink-check methods above. Each tier is still
    # an ordinary `backlink_checks`/`backlink_results` run created via
    # `create_backlink_check`/`save_backlink_result` above; these methods
    # only own the chain-level record and the tier ordering/linking.
    # ----------------------------------------------------------------

    async def create_backlink_chain(self, config: BacklinkChainConfig) -> None:
        """Insert a new backlink-chain record."""
        conn = self._require_conn()
        async with self._write_lock:
            await _BACKLINK_CHAIN_LIFECYCLE.create(conn, config)

    async def link_chain_tier(
        self, chain_id: str, tier_index: int, backlink_id: str, input_path: Path
    ) -> None:
        """Record that ``backlink_id`` (an ordinary backlink-check run) is tier ``tier_index`` of ``chain_id``."""
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute(
                """
                INSERT INTO backlink_chain_tiers (chain_id, tier_index, backlink_id, input_path)
                VALUES (?, ?, ?, ?)
                """,
                (chain_id, tier_index, backlink_id, str(input_path)),
            )
            await conn.commit()

    async def update_backlink_chain_status(
        self, chain_id: str, status: RunStatus, *, finished: bool = False
    ) -> None:
        """Update a backlink-chain run's lifecycle status, optionally stamping ``finished_at``."""
        conn = self._require_conn()
        async with self._write_lock:
            await _BACKLINK_CHAIN_LIFECYCLE.update_status(conn, chain_id, status, finished=finished)

    async def get_backlink_chain_summary(self, chain_id: str) -> BacklinkChainSummary | None:
        """Compute a chain-level summary (one tier summary per tier, in tier order), for ``bl-chain-stats``."""
        conn = self._require_conn()
        chain_cursor = await conn.execute(
            "SELECT label, domain, status FROM backlink_chains WHERE chain_id = ?",
            (chain_id,),
        )
        chain_row = await chain_cursor.fetchone()
        await chain_cursor.close()
        if chain_row is None:
            return None

        tier_cursor = await conn.execute(
            """
            SELECT backlink_id FROM backlink_chain_tiers
            WHERE chain_id = ? ORDER BY tier_index ASC
            """,
            (chain_id,),
        )
        tier_rows = await tier_cursor.fetchall()
        await tier_cursor.close()

        tiers: list[BacklinkCheckSummary] = []
        for tier_row in tier_rows:
            tier_summary = await self.get_backlink_check_summary(tier_row["backlink_id"])
            if tier_summary is not None:
                tiers.append(tier_summary)

        return BacklinkChainSummary(
            chain_id=chain_id,
            domain=chain_row["domain"],
            label=chain_row["label"],
            status=RunStatus(chain_row["status"]),
            tiers=tiers,
        )

    async def list_backlink_chains(self) -> list[BacklinkChainSummary]:
        """Return summaries for every backlink-chain run recorded in this database, newest first."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT chain_id FROM backlink_chains ORDER BY started_at DESC")
        rows = await cursor.fetchall()
        await cursor.close()
        summaries = []
        for row in rows:
            summary = await self.get_backlink_chain_summary(row["chain_id"])
            if summary is not None:
                summaries.append(summary)
        return summaries

    async def resolve_backlink_chain_id(self, partial_id: str) -> str:
        """Resolve a full or shortened (prefix) chain_id to its full UUID. Mirrors ``resolve_backlink_check_id``."""
        try:
            return await _BACKLINK_CHAIN_LIFECYCLE.resolve_id(self._require_conn(), partial_id)
        except RunLifecycleError as exc:
            raise DatabaseError(str(exc)) from exc

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
        matches = [str(row["scan_id"]) for row in rows]

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

    async def delete_scan(self, scan_id: str) -> int:
        """Permanently delete a scan and every row that belongs to it.

        Relies on the schema's ``ON DELETE CASCADE`` foreign keys (and
        ``PRAGMA foreign_keys=ON``, set in :meth:`connect`) to remove the
        matching ``results`` rows, and in turn their ``chain`` and
        ``headers`` rows, in a single statement — no manual multi-table
        delete logic needed here.

        Note: like any SQLite ``DELETE``, this frees the space *within*
        the database file for reuse by future writes, but does **not**
        shrink the file on disk. Call :meth:`vacuum` afterwards (or pass
        it as a separate step) if reclaiming disk space immediately
        matters more than the cost of a full file rewrite.

        Args:
            scan_id: The scan to delete (must be the full, resolved ID —
                callers should run this through :meth:`resolve_scan_id`
                first if the operator may have supplied a short prefix).

        Returns:
            The number of ``results`` rows that were deleted.

        Raises:
            DatabaseError: If no scan with this ID exists.
        """
        conn = self._require_conn()

        if not await self.scan_exists(scan_id):
            raise DatabaseError(f"No such scan: {scan_id}")

        result_count = await self.get_result_count(scan_id)

        async with self._write_lock:
            await conn.execute("DELETE FROM scan WHERE scan_id = ?", (scan_id,))
            await conn.commit()

        return result_count

    async def vacuum(self) -> None:
        """Rebuild the database file to reclaim space freed by deleted rows.

        SQLite's ``DELETE`` never shrinks the file on disk by itself — the
        freed pages are just marked reusable for future inserts. ``VACUUM``
        rewrites the entire file compactly, which is the only way to
        actually reduce the file size after deleting a scan. This can take
        a while and needs roughly as much free disk space as the database
        itself, so it's an explicit, separate step rather than something
        :meth:`delete_scan` does automatically on every call.
        """
        conn = self._require_conn()
        async with self._write_lock:
            await conn.execute("VACUUM")


__all__ = ["Database", "DatabaseError"]
