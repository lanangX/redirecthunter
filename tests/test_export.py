"""Tests for redirecthunter.export."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from redirecthunter.database import Database
from redirecthunter.export import CSV_COLUMNS, Exporter, ExportError, ExportFilter
from redirecthunter.models import ExportFormat, HTTPMethod, RedirectResult, RedirectType


@pytest.fixture
async def populated_db(tmp_path: Path, sample_config):
    database = Database(tmp_path / "export_source.db")
    await database.connect()
    await database.create_scan(sample_config, total_urls=2)
    await database.save_result(
        RedirectResult(
            scan_id=sample_config.scan_id,
            source_url="https://a.com/go?url={TARGET}",
            expanded_url="https://a.com/go?url=https://example.org",
            http_method=HTTPMethod.HEAD,
            status_code=301,
            redirect_type=RedirectType.HTTP_301,
            final_url="https://example.org/",
            alive=True,
            latency_ms=90.0,
        ),
        final_headers={"Server": "nginx"},
    )
    await database.save_result(
        RedirectResult(
            scan_id=sample_config.scan_id,
            source_url="https://b.com/go?url={TARGET}, with a comma",
            expanded_url="https://b.com/go?url=https://example.org",
            http_method=HTTPMethod.HEAD,
            alive=False,
            latency_ms=3000.0,
            error="ConnectTimeout",
        )
    )
    yield database, sample_config.scan_id
    await database.close()


class TestExporter:
    async def test_csv_export_row_count_and_quoting(self, populated_db, tmp_path: Path) -> None:
        db, scan_id = populated_db
        exporter = Exporter(db)
        output = tmp_path / "out.csv"
        count = await exporter.export(scan_id, output, ExportFormat.CSV)
        assert count == 2

        with output.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[0] == list(CSV_COLUMNS)
        assert len(rows) == 3
        # Confirm the comma-containing source_url round-trips via proper CSV quoting
        source_urls = [row[2] for row in rows[1:]]
        assert "https://b.com/go?url={TARGET}, with a comma" in source_urls

    async def test_json_export_preserves_nested_structure(self, populated_db, tmp_path: Path) -> None:
        db, scan_id = populated_db
        exporter = Exporter(db)
        output = tmp_path / "out.json"
        count = await exporter.export(scan_id, output, ExportFormat.JSON)
        assert count == 2

        with output.open("rb") as fh:
            parsed = json.load(fh)
        assert len(parsed) == 2
        by_source = {row["source_url"]: row for row in parsed}
        assert by_source["https://a.com/go?url={TARGET}"]["redirect_type"] == "301_moved_permanently"
        assert by_source["https://b.com/go?url={TARGET}, with a comma"]["error"] == "ConnectTimeout"

    async def test_sqlite_export_is_standalone_and_complete(self, populated_db, tmp_path: Path) -> None:
        db, scan_id = populated_db
        exporter = Exporter(db)
        output = tmp_path / "out.db"
        count = await exporter.export(scan_id, output, ExportFormat.SQLITE)
        assert count == 2

        exported_db = Database(output)
        await exported_db.connect()
        try:
            summary = await exported_db.get_scan_summary(scan_id)
            assert summary.completed == 2
            assert summary.alive == 1
        finally:
            await exported_db.close()

    async def test_status_code_filter_exact_match(self, populated_db, tmp_path: Path) -> None:
        db, scan_id = populated_db
        exporter = Exporter(db)
        output = tmp_path / "301.csv"
        count = await exporter.export(
            scan_id, output, ExportFormat.CSV, ExportFilter(status_codes=frozenset({301}))
        )
        assert count == 1
        with output.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[1][CSV_COLUMNS.index("status_code")] == "301"

    async def test_status_code_filter_class_match(self, populated_db, tmp_path: Path) -> None:
        db, scan_id = populated_db
        exporter = Exporter(db)
        output = tmp_path / "3xx.csv"
        count = await exporter.export(
            scan_id, output, ExportFormat.CSV, ExportFilter(status_classes=frozenset({3}))
        )
        assert count == 1

        output_miss = tmp_path / "5xx.csv"
        count_miss = await exporter.export(
            scan_id, output_miss, ExportFormat.CSV, ExportFilter(status_classes=frozenset({5}))
        )
        assert count_miss == 0

    async def test_status_code_filter_excludes_results_with_no_status(
        self, populated_db, tmp_path: Path
    ) -> None:
        # The second fixture result never got a response (ConnectTimeout), so
        # status_code is None -- it must never match any status-code filter.
        db, scan_id = populated_db
        exporter = Exporter(db)
        output = tmp_path / "any_status.csv"
        count = await exporter.export(
            scan_id,
            output,
            ExportFormat.CSV,
            ExportFilter(status_codes=frozenset({301}), status_classes=frozenset({2, 4, 5})),
        )
        assert count == 1

    async def test_has_link_only_filters_out_results_without_body_link(
        self, populated_db, tmp_path: Path
    ) -> None:
        # Neither fixture result has a body_link (both are HEAD requests with
        # no body), so --has-link-only must export zero rows -- this is the
        # expected, documented behavior for HEAD-method scans.
        db, scan_id = populated_db
        exporter = Exporter(db)
        output = tmp_path / "links.csv"
        count = await exporter.export(scan_id, output, ExportFormat.CSV, ExportFilter(has_link_only=True))
        assert count == 0

    async def test_filtered_sqlite_export_raises(self, populated_db, tmp_path: Path) -> None:
        db, scan_id = populated_db
        exporter = Exporter(db)
        output = tmp_path / "filtered.db"
        with pytest.raises(ExportError, match="not supported with --format sqlite"):
            await exporter.export(
                scan_id, output, ExportFormat.SQLITE, ExportFilter(status_codes=frozenset({301}))
            )
