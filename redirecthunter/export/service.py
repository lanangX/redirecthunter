"""Format dispatch: :class:`Exporter` is the one public entry point callers use."""

from __future__ import annotations

from pathlib import Path

from redirecthunter.database import Database
from redirecthunter.export.csv_writer import write_csv
from redirecthunter.export.filters import ExportError, ExportFilter
from redirecthunter.export.json_writer import write_json
from redirecthunter.models import ExportFormat


class Exporter:
    """Streams a scan's results to CSV, JSON, or a standalone SQLite file.

    Takes a :class:`~redirecthunter.database.Database` via constructor
    injection — the exporter itself performs no direct file-format-specific
    SQL; the SQLite export path delegates to
    :meth:`~redirecthunter.database.Database.export_scan_to_sqlite`, keeping
    all raw SQL inside the persistence layer. The CSV and JSON paths
    likewise delegate to :func:`~redirecthunter.export.csv_writer.write_csv`
    and :func:`~redirecthunter.export.json_writer.write_json` -- this class
    only decides *which* writer to call.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def export(
        self,
        scan_id: str,
        output_path: Path,
        fmt: ExportFormat,
        result_filter: ExportFilter | None = None,
    ) -> int:
        """Export results recorded for ``scan_id`` in the requested format.

        Args:
            scan_id: The scan to export.
            output_path: Destination file path. Parent directories are
                created automatically if missing.
            fmt: One of :class:`~redirecthunter.models.ExportFormat`.
            result_filter: Optional :class:`ExportFilter` restricting which
                rows are written (e.g. redirects only). ``None`` or an
                empty filter exports every recorded result, matching the
                original unfiltered behavior.

        Returns:
            The number of result rows exported.

        Raises:
            ExportError: If ``fmt`` is not a recognized export format, or
                if a non-empty ``result_filter`` is given for
                :attr:`~redirecthunter.models.ExportFormat.SQLITE` --
                filtered SQLite export is not currently supported because
                the fast path is a raw-SQL table copy rather than a
                per-row streaming write. Use CSV or JSON for filtered
                exports, or export SQLite unfiltered and query it directly.
        """
        result_filter = result_filter or ExportFilter()

        if fmt is ExportFormat.CSV:
            return await write_csv(self._database, scan_id, output_path, result_filter)
        if fmt is ExportFormat.JSON:
            return await write_json(self._database, scan_id, output_path, result_filter)
        if fmt is ExportFormat.SQLITE:
            if not result_filter.is_empty:
                raise ExportError(
                    "Filtered export (--alive-only / --redirects-only / --cloudflare-only / "
                    "--has-link-only / --status-code) is not supported with --format sqlite. "
                    "Export to CSV or JSON instead, or export the full SQLite file and query "
                    "it directly."
                )
            return await self._database.export_scan_to_sqlite(scan_id, output_path)
        raise ExportError(f"Unsupported export format: {fmt}")  # pragma: no cover - exhaustive enum


__all__ = ["Exporter"]
