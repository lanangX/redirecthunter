"""Tests for redirecthunter.database's crawl-mode CRUD methods."""

from __future__ import annotations

from pathlib import Path

import pytest

from redirecthunter.database import Database, DatabaseError
from redirecthunter.models import (
    CrawlConfig,
    CrawlLinkResult,
    CrawlPageResult,
    CrawlSeedMode,
    LinkKind,
    PageIssue,
    RunStatus,
)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


class TestCrawlPersistence:
    async def test_create_and_fetch_crawl(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        await db.create_crawl(sample_crawl_config)
        assert await db.crawl_exists(sample_crawl_config.crawl_id) is True
        assert await db.crawl_exists("nonexistent") is False

        fetched = await db.get_crawl_config(sample_crawl_config.crawl_id)
        assert fetched is not None
        assert fetched.seed_url == sample_crawl_config.seed_url
        assert fetched.max_depth == sample_crawl_config.max_depth

    async def test_update_crawl_status(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        await db.create_crawl(sample_crawl_config)
        await db.update_crawl_status(sample_crawl_config.crawl_id, RunStatus.COMPLETED, finished=True)

        summary = await db.get_crawl_summary(sample_crawl_config.crawl_id)
        assert summary is not None
        assert summary.status is RunStatus.COMPLETED
        assert summary.finished_at is not None

    async def test_save_and_stream_pages(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        await db.create_crawl(sample_crawl_config)
        page = CrawlPageResult(
            crawl_id=sample_crawl_config.crawl_id,
            url="https://example.test/",
            depth=0,
            status_code=200,
            alive=True,
            title="Home",
            title_length=4,
            meta_description=None,
            h1_texts=["Welcome"],
            h1_count=1,
            internal_link_count=2,
            external_link_count=1,
            issues=[PageIssue.MISSING_META_DESCRIPTION, PageIssue.TITLE_TOO_SHORT],
        )
        await db.save_crawl_page(page)

        pages = [p async for p in db.iter_crawl_pages(sample_crawl_config.crawl_id)]
        assert len(pages) == 1
        reloaded = pages[0]
        assert reloaded.url == page.url
        assert reloaded.h1_texts == ["Welcome"]
        assert set(reloaded.issues) == {PageIssue.MISSING_META_DESCRIPTION, PageIssue.TITLE_TOO_SHORT}

    async def test_save_and_stream_links(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        await db.create_crawl(sample_crawl_config)
        alive_link = CrawlLinkResult(
            crawl_id=sample_crawl_config.crawl_id,
            source_page_url="https://example.test/",
            target_url="https://example.test/about",
            raw_href="/about",
            link_kind=LinkKind.INTERNAL,
            rel="nofollow",
            target_attr="_blank",
            status_code=200,
            is_broken=False,
        )
        broken_link = CrawlLinkResult(
            crawl_id=sample_crawl_config.crawl_id,
            source_page_url="https://example.test/",
            target_url="https://example.test/dead",
            raw_href="/dead",
            link_kind=LinkKind.INTERNAL,
            status_code=404,
            is_broken=True,
        )
        await db.save_crawl_link(alive_link)
        await db.save_crawl_link(broken_link)

        all_links = [link async for link in db.iter_crawl_links(sample_crawl_config.crawl_id)]
        assert len(all_links) == 2

        broken_only = [link async for link in db.iter_crawl_links(sample_crawl_config.crawl_id, broken_only=True)]
        assert len(broken_only) == 1
        assert broken_only[0].target_url == "https://example.test/dead"
        assert broken_only[0].rel is None
        assert broken_only[0].target_attr is None

        reloaded_alive = next(link for link in all_links if link.target_url == "https://example.test/about")
        assert reloaded_alive.rel == "nofollow"
        assert reloaded_alive.target_attr == "_blank"

    async def test_summary_aggregates_pages_and_links(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        await db.create_crawl(sample_crawl_config)

        # Two pages sharing the same title -- should count as a duplicate pair.
        for i, title in enumerate(["Shared Title Here", "Shared Title Here", "Unique Title Value"]):
            await db.save_crawl_page(
                CrawlPageResult(
                    crawl_id=sample_crawl_config.crawl_id,
                    url=f"https://example.test/page-{i}",
                    depth=0,
                    status_code=200,
                    alive=True,
                    title=title,
                    title_length=len(title),
                    issues=[] if i != 2 else [PageIssue.MISSING_H1],
                )
            )
        await db.save_crawl_link(
            CrawlLinkResult(
                crawl_id=sample_crawl_config.crawl_id,
                source_page_url="https://example.test/page-0",
                target_url="https://example.test/oops",
                raw_href="/oops",
                link_kind=LinkKind.EXTERNAL,
                status_code=500,
                is_broken=True,
            )
        )

        summary = await db.get_crawl_summary(sample_crawl_config.crawl_id)
        assert summary is not None
        assert summary.pages_crawled == 3
        assert summary.pages_alive == 3
        assert summary.pages_duplicate_title == 2
        assert summary.links_checked == 1
        assert summary.broken_links == 1
        assert summary.broken_external_links == 1
        assert summary.broken_internal_links == 0

    async def test_broken_links_only_filter_includes_promoted_dead_pages(
        self, db: Database, sample_crawl_config: CrawlConfig
    ) -> None:
        """A dead internal page (status >= 400) counts as a broken outlink of
        whatever page discovered it, even with no crawl_links row -- see
        Crawler._enqueue_discovered_link and CrawlLinkResult's docstring."""
        await db.create_crawl(sample_crawl_config)
        await db.save_crawl_page(
            CrawlPageResult(
                crawl_id=sample_crawl_config.crawl_id,
                url="https://example.test/",
                depth=0,
                status_code=200,
                alive=True,
            )
        )
        await db.save_crawl_page(
            CrawlPageResult(
                crawl_id=sample_crawl_config.crawl_id,
                url="https://example.test/dead",
                depth=1,
                discovered_from="https://example.test/",
                status_code=404,
                alive=True,
            )
        )
        await db.save_crawl_page(
            CrawlPageResult(
                crawl_id=sample_crawl_config.crawl_id,
                url="https://example.test/fine",
                depth=1,
                discovered_from="https://example.test/",
                status_code=200,
                alive=True,
            )
        )

        flagged = [p async for p in db.iter_crawl_pages(sample_crawl_config.crawl_id, broken_links_only=True)]
        assert {p.url for p in flagged} == {"https://example.test/"}

    async def test_resolve_crawl_id_prefix(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        await db.create_crawl(sample_crawl_config)
        prefix = sample_crawl_config.crawl_id[:8]
        resolved = await db.resolve_crawl_id(prefix)
        assert resolved == sample_crawl_config.crawl_id

    async def test_resolve_crawl_id_no_match_raises(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        await db.create_crawl(sample_crawl_config)
        with pytest.raises(DatabaseError):
            await db.resolve_crawl_id("doesnotexist")

    async def test_delete_crawl_cascades(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        await db.create_crawl(sample_crawl_config)
        await db.save_crawl_page(
            CrawlPageResult(crawl_id=sample_crawl_config.crawl_id, url="https://example.test/", depth=0)
        )

        deleted_count = await db.delete_crawl(sample_crawl_config.crawl_id)
        assert deleted_count == 1
        assert await db.crawl_exists(sample_crawl_config.crawl_id) is False
        pages = [p async for p in db.iter_crawl_pages(sample_crawl_config.crawl_id)]
        assert pages == []

    async def test_list_crawls(self, db: Database, sample_crawl_config: CrawlConfig) -> None:
        other = sample_crawl_config.model_copy(
            update={"crawl_id": "11111111-1111-1111-1111-111111111111", "seed_mode": CrawlSeedMode.URL_LIST}
        )
        await db.create_crawl(sample_crawl_config)
        await db.create_crawl(other)

        summaries = await db.list_crawls()
        assert {s.crawl_id for s in summaries} == {sample_crawl_config.crawl_id, other.crawl_id}


class TestCrawlLinksMigration:
    async def test_old_schema_db_gets_rel_and_target_attr_columns(
        self, tmp_path: Path, sample_crawl_config: CrawlConfig
    ) -> None:
        """A crawl_links table created before rel/target_attr existed should be
        upgraded via ALTER TABLE on connect, not raise 'no such column'."""
        import aiosqlite

        db_path = tmp_path / "old.db"
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE crawl_links (
                    link_id           TEXT PRIMARY KEY,
                    crawl_id          TEXT NOT NULL,
                    source_page_url   TEXT NOT NULL,
                    target_url        TEXT NOT NULL,
                    raw_href          TEXT NOT NULL,
                    link_kind         TEXT NOT NULL,
                    anchor_text       TEXT,
                    status_code       INTEGER,
                    is_broken         INTEGER NOT NULL,
                    redirected        INTEGER NOT NULL DEFAULT 0,
                    final_url         TEXT,
                    error             TEXT,
                    latency_ms        REAL NOT NULL DEFAULT 0,
                    checked_at        TEXT NOT NULL
                );
                """
            )
            await conn.commit()

        database = Database(db_path)
        await database.connect()
        try:
            await database.create_crawl(sample_crawl_config)
            link = CrawlLinkResult(
                crawl_id=sample_crawl_config.crawl_id,
                source_page_url="https://example.test/",
                target_url="https://example.test/about",
                raw_href="/about",
                link_kind=LinkKind.INTERNAL,
                rel="nofollow",
                target_attr="_blank",
                status_code=200,
                is_broken=False,
            )
            await database.save_crawl_link(link)
            reloaded = [link async for link in database.iter_crawl_links(sample_crawl_config.crawl_id)]
            assert reloaded[0].rel == "nofollow"
            assert reloaded[0].target_attr == "_blank"
        finally:
            await database.close()

