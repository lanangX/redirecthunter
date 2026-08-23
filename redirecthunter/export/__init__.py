"""Streaming, memory-bounded result exporters for RedirectHunter.

Every format streams from :meth:`~redirecthunter.database.Database.iter_results`
(or, for SQLite, a pure-SQL ``ATTACH DATABASE`` copy) rather than
materializing a full 100k-row result set as a Python list before writing —
consistent with the memory-efficiency requirement that runs through the
whole project.

One module per concern, mirroring :mod:`redirecthunter.plugins`:

    - :mod:`filters`     — :class:`ExportFilter` / :class:`ExportError`,
      the row-level "does this result pass?" logic shared by ``export``
      and ``show``.
    - :mod:`csv_writer`  — the CSV column contract and streaming CSV writer.
    - :mod:`json_writer` — the streaming JSON-array writer.
    - :mod:`service`     — :class:`Exporter`, which dispatches to the two
      writers above (or to :meth:`~redirecthunter.database.Database.export_scan_to_sqlite`
      for SQLite) based on the requested :class:`~redirecthunter.models.ExportFormat`.

Only the names re-exported below are the package's public API — ``cli.py``
and anything else outside this package should import from
``redirecthunter.export`` directly rather than reaching into a specific
submodule.
"""

from __future__ import annotations

from redirecthunter.export.csv_writer import CSV_COLUMNS
from redirecthunter.export.filters import ExportError, ExportFilter
from redirecthunter.export.service import Exporter

__all__ = ["CSV_COLUMNS", "ExportError", "ExportFilter", "Exporter"]
