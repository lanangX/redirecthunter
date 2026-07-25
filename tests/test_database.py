"""Tests for redirecthunter.database."""

from __future__ import annotations

from pathlib import Path

import pytest

from redirecthunter.database import Database, DatabaseError
from redirecthunter.models import (
    CloudflareStatus,
    FingerprintInfo,
    HTTPMethod,
    RedirectHop,
    RedirectResult,
    RedirectType,
    ScanStatus,
)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


class TestDatabase:
    async def test_create_and_fetch_scan(self, db: Database, sample_config) -> None:
        await db.create_scan(sample_config, total_urls=10)
        assert await db.scan_exists(sample_config.scan_id) is True
        assert await db.scan_exists("nonexistent") is False

        fetched = await db.get_scan_config(sample_config.scan_id)
        assert fetched is not None
        assert fetched.workers == sample_config.workers
        assert fetched.target == sample_config.target

    async def test_save_and_reload_result_with_chain(self, db: Database, sample_config) -> None:
        await db.create_scan(sample_config, total_urls=1)
        result = RedirectResult(
            scan_id=sample_config.scan_id,
            source_url="https://a.com/go",
            expanded_url="https://a.com/go?url=https://example.org",
            http_method=HTTPMethod.HEAD,
            status_code=301,
            redirect_type=RedirectType.HTTP_301,
            final_url="https://example.org/",
            redirect_chain=[
                RedirectHop(
                    hop_index=0, url="https://a.com/go", status_code=301,
                    redirect_type=RedirectType.HTTP_301, location_header="https://example.org/",
                    server_header="nginx", latency_ms=10.0,
                )
            ],
            hop_count=1,
            server="cloudflare",
            cookies={"session": "abc"},
            fingerprint=FingerprintInfo(
                detected_software="Cloudflare",
                cloudflare=CloudflareStatus(is_cloudflare=True, cf_ray_id="ray1"),
            ),
            alive=True,
            latency_ms=90.0,
        )
        await db.save_result(result, final_headers={"Server": "cloudflare"})

        reloaded = [r async for r in db.iter_results(sample_config.scan_id)]
        assert len(reloaded) == 1
        assert reloaded[0].hop_count == 1
        assert reloaded[0].redirect_chain[0].status_code == 301
        assert reloaded[0].fingerprint.cloudflare.is_cloudflare is True
        assert reloaded[0].cookies == {"session": "abc"}

    async def test_resume_filters_completed_urls(self, db: Database, sample_config) -> None:
        await db.create_scan(sample_config, total_urls=2)
        await db.save_result(
            RedirectResult(
                scan_id=sample_config.scan_id, source_url="https://a.com", expanded_url="https://a.com",
                http_method=HTTPMethod.HEAD, alive=True, latency_ms=10.0,
            )
        )
        completed = await db.get_completed_source_urls(sample_config.scan_id)
        assert completed == {"https://a.com"}

    async def test_scan_summary_aggregation(self, db: Database, sample_config) -> None:
        await db.create_scan(sample_config, total_urls=2)
        await db.save_result(
            RedirectResult(
                scan_id=sample_config.scan_id, source_url="https://a.com", expanded_url="https://a.com",
                http_method=HTTPMethod.HEAD, status_code=200, alive=True, latency_ms=10.0,
            )
        )
        await db.save_result(
            RedirectResult(
                scan_id=sample_config.scan_id, source_url="https://b.com", expanded_url="https://b.com",
                http_method=HTTPMethod.HEAD, alive=False, latency_ms=5000.0, error="Timeout",
            )
        )
        await db.update_scan_status(sample_config.scan_id, ScanStatus.COMPLETED, finished=True)

        summary = await db.get_scan_summary(sample_config.scan_id)
        assert summary.completed == 2
        assert summary.alive == 1
        assert summary.dead == 1
        assert summary.status == ScanStatus.COMPLETED
        assert summary.finished_at is not None

    async def test_export_scan_to_sqlite_standalone_file(self, db: Database, sample_config, tmp_path: Path) -> None:
        await db.create_scan(sample_config, total_urls=1)
        await db.save_result(
            RedirectResult(
                scan_id=sample_config.scan_id, source_url="https://a.com", expanded_url="https://a.com",
                http_method=HTTPMethod.HEAD, status_code=200, alive=True, latency_ms=10.0,
            ),
            final_headers={"Server": "nginx"},
        )

        dest = tmp_path / "exported.db"
        count = await db.export_scan_to_sqlite(sample_config.scan_id, dest)
        assert count == 1
        assert dest.exists()

        exported_db = Database(dest)
        await exported_db.connect()
        try:
            summary = await exported_db.get_scan_summary(sample_config.scan_id)
            assert summary is not None
            assert summary.completed == 1
        finally:
            await exported_db.close()

    async def test_export_nonexistent_scan_raises(self, db: Database, tmp_path: Path) -> None:
        with pytest.raises(DatabaseError):
            await db.export_scan_to_sqlite("nonexistent", tmp_path / "out.db")

    async def test_resolve_scan_id_full_id(self, db: Database, sample_config) -> None:
        await db.create_scan(sample_config, total_urls=1)
        resolved = await db.resolve_scan_id(sample_config.scan_id)
        assert resolved == sample_config.scan_id

    async def test_resolve_scan_id_short_prefix(self, db: Database, sample_config) -> None:
        await db.create_scan(sample_config, total_urls=1)
        short = sample_config.scan_id[:8]
        resolved = await db.resolve_scan_id(short)
        assert resolved == sample_config.scan_id

    async def test_resolve_scan_id_no_match_raises(self, db: Database, sample_config) -> None:
        await db.create_scan(sample_config, total_urls=1)
        with pytest.raises(DatabaseError):
            await db.resolve_scan_id("zzzzzzzz")

    async def test_resolve_scan_id_ambiguous_prefix_raises(self, db: Database, sample_config) -> None:
        await db.create_scan(sample_config, total_urls=1)
        colliding_config = sample_config.model_copy(
            update={"scan_id": sample_config.scan_id[:4] + "0000-0000-0000-000000000000"}
        )
        await db.create_scan(colliding_config, total_urls=1)
        with pytest.raises(DatabaseError):
            await db.resolve_scan_id(sample_config.scan_id[:4])

    async def test_uses_connected_error_without_connect(self, tmp_path: Path) -> None:
        database = Database(tmp_path / "unopened.db")
        with pytest.raises(DatabaseError):
            await database.scan_exists("anything")
