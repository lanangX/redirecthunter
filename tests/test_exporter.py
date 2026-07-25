"""Tests for redirecthunter.exporter."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from redirecthunter.database import Database
from redirecthunter.exporter import CSV_COLUMNS, Exporter
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
