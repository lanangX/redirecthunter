"""Streaming CSV export: one row written per record as it's fetched."""

from __future__ import annotations

import csv
from pathlib import Path

from redirecthunter.database import Database
from redirecthunter.export.filters import ExportFilter
from redirecthunter.models import RedirectResult

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
    "body_link",
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
        result.body_link or "",
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


async def write_csv(
    database: Database, scan_id: str, output_path: Path, result_filter: ExportFilter
) -> int:
    """Stream ``scan_id``'s results to CSV at ``output_path``, filtered by ``result_filter``.

    Returns the number of rows written (header not included).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        async for result in database.iter_results(scan_id):
            if not result_filter.matches(result):
                continue
            writer.writerow(_result_to_csv_row(result))
            count += 1
    return count


__all__ = ["CSV_COLUMNS", "write_csv"]
