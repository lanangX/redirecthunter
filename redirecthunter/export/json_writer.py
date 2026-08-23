"""Streaming JSON export: one array element written per record as it's fetched."""

from __future__ import annotations

from pathlib import Path

import orjson

from redirecthunter.database import Database
from redirecthunter.export.filters import ExportFilter


async def write_json(
    database: Database, scan_id: str, output_path: Path, result_filter: ExportFilter
) -> int:
    """Stream ``scan_id``'s results to a JSON array at ``output_path``, filtered by ``result_filter``.

    Manually manages the array's opening/closing brackets and
    comma-separation instead of building a full Python list and calling
    ``orjson.dumps()`` once — that would require holding every result in
    memory simultaneously, which is exactly what streaming avoids.

    Returns the number of array elements written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("wb") as fh:
        fh.write(b"[")
        first = True
        async for result in database.iter_results(scan_id):
            if not result_filter.matches(result):
                continue
            if not first:
                fh.write(b",")
            fh.write(orjson.dumps(result.model_dump(mode="json")))
            first = False
            count += 1
        fh.write(b"]")
    return count


__all__ = ["write_json"]
