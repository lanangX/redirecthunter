"""Streaming, memory-bounded result exporters for RedirectHunter.

Every format streams from :meth:`~redirecthunter.database.Database.iter_results`
(or, for SQLite, a pure-SQL ``ATTACH DATABASE`` copy) rather than
materializing a full 100k-row result set as a Python list before writing —
consistent with the memory-efficiency requirement that runs through the
whole project.
"""

from __future__ import annotations

import csv
from pathlib import Path

import orjson

from redirecthunter.database import Database
from redirecthunter.models import ExportFormat, RedirectResult

#: Column order for CSV export. Nested structures (redirect chain, full
#: cookie jar) are intentionally summarized rather than flattened into
#: many sparse columns — CSV is the "quick spreadsheet view" format;
#: operators who need the full nested detail should use ``--format json``
#: or ``--format sqlite`` instead.
CSV_COLUMNS: tuple[str, ...] = (
    "result_id",
    "scan_id",
    "source_url",
    "expanded_url",
    "http_method",
    "status_code",
    "redirect_type",
    "location",
    "final_url",
    "hop_count",
    "server",
    "content_type",
    "content_length",
    "detected_software",
    "cloudflare_protected",
    "alive",
    "latency_ms",
    "error",
    "timestamp",
)


class ExportError(Exception):
    """Raised when an export cannot be completed."""


def _result_to_csv_row(result: RedirectResult) -> list[str | int | float]:
    """Flatten one RedirectResult into a CSV row matching CSV_COLUMNS."""
    return [
        result.result_id,
        result.scan_id,
        result.source_url,
        result.expanded_url,
        result.http_method.value,
        result.status_code if result.status_code is not None else "",
        result.redirect_type.value,
        result.location or "",
        result.final_url or "",
        result.hop_count,
        result.server or "",
        result.content_type or "",
        result.content_length if result.content_length is not None else "",
        result.fingerprint.detected_software or "",
        "yes" if result.fingerprint.cloudflare.is_cloudflare else "no",
        "yes" if result.alive else "no",
        round(result.latency_ms, 2),
        result.error or "",
        result.timestamp.isoformat(),
    ]


class Exporter:
    """Streams a scan's results to CSV, JSON, or a standalone SQLite file.

    Takes a :class:`~redirecthunter.database.Database` via constructor
    injection — the exporter itself performs no direct file-format-specific
    SQL; the SQLite export path delegates to
    :meth:`~redirecthunter.database.Database.export_scan_to_sqlite`, keeping
    all raw SQL inside the persistence layer.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def export(self, scan_id: str, output_path: Path, fmt: ExportFormat) -> int:
        """Export every result recorded for ``scan_id`` in the requested format.

        Args:
            scan_id: The scan to export.
            output_path: Destination file path. Parent directories are
                created automatically if missing.
            fmt: One of :class:`~redirecthunter.models.ExportFormat`.

        Returns:
            The number of result rows exported.

        Raises:
            ExportError: If ``fmt`` is not a recognized export format.
        """
        if fmt is ExportFormat.CSV:
            return await self._export_csv(scan_id, output_path)
        if fmt is ExportFormat.JSON:
            return await self._export_json(scan_id, output_path)
        if fmt is ExportFormat.SQLITE:
            return await self._database.export_scan_to_sqlite(scan_id, output_path)
        raise ExportError(f"Unsupported export format: {fmt}")  # pragma: no cover - exhaustive enum

    async def _export_csv(self, scan_id: str, output_path: Path) -> int:
        """Stream results to CSV, one row written per record as it's fetched."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_COLUMNS)
            async for result in self._database.iter_results(scan_id):
                writer.writerow(_result_to_csv_row(result))
                count += 1
        return count

    async def _export_json(self, scan_id: str, output_path: Path) -> int:
        """Stream results to a JSON array, one object written per record as it's fetched.

        Manually manages the array's opening/closing brackets and
        comma-separation instead of building a full Python list and calling
        ``orjson.dumps()`` once — that would require holding every result
        in memory simultaneously, which is exactly what streaming avoids.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with output_path.open("wb") as fh:
            fh.write(b"[")
            first = True
            async for result in self._database.iter_results(scan_id):
                if not first:
                    fh.write(b",")
                fh.write(orjson.dumps(result.model_dump(mode="json")))
                first = False
                count += 1
            fh.write(b"]")
        return count


__all__ = ["Exporter", "ExportError", "CSV_COLUMNS"]
