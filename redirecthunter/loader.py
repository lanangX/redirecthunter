"""Candidate-URL input loading for TXT, CSV, JSON, and SQLite files.

Kept separate from ``cli.py`` so the CLI module stays focused on Typer
command wiring and Rich display, rather than absorbing four different
file-format parsers. Every loader is a lazy generator (or, for JSON, as
close to lazy as the ``orjson``/stdlib-``json`` ecosystem allows without an
extra streaming-JSON dependency) so :meth:`redirecthunter.engine.Engine.run`
can start processing the first candidate before the rest of a 100k-line
input file has even been read.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import orjson

from redirecthunter.models import CandidateURL, InputFormat, ScanConfig

#: Valid SQL identifier pattern, used to safely interpolate a user-supplied
#: table name into a query. Column names never need interpolation — rows
#: are fetched with ``SELECT *`` and the target column is looked up by name
#: in Python, avoiding injection risk entirely for that half of the input.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LoaderError(Exception):
    """Raised when a candidate-URL input file cannot be read or parsed."""


def _validate_identifier(name: str, kind: str) -> str:
    """Validate a SQL identifier before interpolating it into a query string."""
    if not _IDENTIFIER_PATTERN.match(name):
        raise LoaderError(
            f"Invalid {kind} name {name!r}: must start with a letter or underscore and "
            f"contain only letters, digits, and underscores."
        )
    return name


def _iter_txt(path: Path) -> Iterator[CandidateURL]:
    """Yield one CandidateURL per non-empty, non-comment line.

    Lines starting with ``#`` (after stripping leading whitespace) are
    treated as comments and skipped, a common convention for plain-text
    URL lists that lets operators annotate or temporarily disable entries.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                yield CandidateURL(raw_url=stripped)
    except OSError as exc:
        raise LoaderError(f"Could not read input file {path}: {exc}") from exc


def _iter_csv(path: Path, column: str) -> Iterator[CandidateURL]:
    """Yield one CandidateURL per data row of a CSV file.

    Header detection is deterministic rather than heuristic: if the first
    row contains a cell matching ``column`` (case-insensitive), that row is
    treated as a header and URLs are read from the matching column, with
    every other column kept as row metadata. Otherwise the file is treated
    as headerless and the first column of every row (including the first)
    is used as the URL.

    ``csv.Sniffer().has_header()`` was deliberately not used here — it is
    a statistical heuristic (comparing apparent column "types" across
    rows) that is well known to misclassify short, uniform files. A
    3-column, 4-data-row CSV with a plain string header is exactly the
    case it gets wrong in practice, silently treating the header row as
    data. Checking for the configured column name directly is exact, not
    probabilistic.

    Reads the file lazily, one row at a time — deciding whether row 0 is a
    header requires looking at it, but nothing beyond that single row is
    ever buffered.
    """
    try:
        fh = path.open("r", newline="", encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise LoaderError(f"Could not read input file {path}: {exc}") from exc

    with fh:
        reader = csv.reader(fh)
        try:
            first_row = next(reader)
        except StopIteration:
            return  # empty file

        first_row_lower = [cell.strip().lower() for cell in first_row]
        has_header = column.lower() in first_row_lower

        if has_header:
            header: list[str] | None = first_row
            col_index = first_row_lower.index(column.lower())
        else:
            header = None
            col_index = 0
            # first_row is itself a data row (not a header) -- process it too.
            if col_index < len(first_row):
                url = first_row[col_index].strip()
                if url:
                    yield CandidateURL(raw_url=url)

        for row in reader:
            if not row or col_index >= len(row):
                continue
            url = row[col_index].strip()
            if not url:
                continue
            if header:
                metadata = {
                    h: v for i, (h, v) in enumerate(zip(header, row, strict=False)) if h and i != col_index
                }
                yield CandidateURL(raw_url=url, row_metadata=metadata)
            else:
                yield CandidateURL(raw_url=url)


def _iter_json(path: Path) -> Iterator[CandidateURL]:
    """Yield one CandidateURL per entry of a top-level JSON array.

    Each array element may be either a plain string (the URL itself) or an
    object with a ``url`` key (any other keys are kept as row metadata).
    Malformed individual entries are skipped rather than aborting the
    entire load, consistent with this project's general policy of never
    letting one bad row abort a large batch job.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LoaderError(f"Could not read input file {path}: {exc}") from exc

    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise LoaderError(f"Malformed JSON in {path}: {exc}") from exc

    if not isinstance(data, list):
        raise LoaderError(
            f"JSON input file {path} must contain a top-level array, got {type(data).__name__}."
        )

    for item in data:
        if isinstance(item, str):
            url = item.strip()
            if url:
                yield CandidateURL(raw_url=url)
        elif isinstance(item, dict) and isinstance(item.get("url"), str):
            url = item["url"].strip()
            if url:
                metadata = {k: v for k, v in item.items() if k != "url"}
                yield CandidateURL(raw_url=url, row_metadata=metadata)
        # Silently skip anything else (numbers, null, malformed objects).


def _iter_sqlite(path: Path, table: str, column: str) -> Iterator[CandidateURL]:
    """Yield one CandidateURL per row of a SQLite input table.

    Uses the synchronous stdlib ``sqlite3`` module rather than
    ``aiosqlite`` — this reads a small, local, one-off input file (not the
    application's own results database), and a lazy synchronous generator
    integrates directly with :meth:`redirecthunter.engine.Engine.run`'s
    plain-``Iterable`` code path without requiring an async wrapper.
    """
    validated_table = _validate_identifier(table, "table")

    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error as exc:
        raise LoaderError(f"Could not open SQLite input file {path}: {exc}") from exc

    try:
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(f"SELECT * FROM {validated_table}")  # noqa: S608 - identifier validated above
        except sqlite3.Error as exc:
            raise LoaderError(f"Could not query table '{table}' in {path}: {exc}") from exc

        column_names = [description[0] for description in cursor.description]
        if column not in column_names:
            raise LoaderError(
                f"Column '{column}' not found in table '{table}' of {path}. "
                f"Available columns: {', '.join(column_names)}."
            )

        for row in cursor:
            raw_value = row[column]
            url = str(raw_value).strip() if raw_value is not None else ""
            if not url:
                continue
            metadata = {name: row[name] for name in column_names if name != column}
            yield CandidateURL(raw_url=url, row_metadata=metadata)
    finally:
        conn.close()


def load_candidates(config: ScanConfig) -> Iterator[CandidateURL]:
    """Lazily yield candidate URLs from ``config.input_path``, dispatched by ``config.input_format``.

    Args:
        config: The resolved scan configuration.

    Yields:
        One :class:`~redirecthunter.models.CandidateURL` per valid input row.

    Raises:
        LoaderError: If the input file is missing, unreadable, malformed,
            or (for SQLite input) references a nonexistent table/column.
    """
    if not config.input_path.exists():
        raise LoaderError(f"Input file not found: {config.input_path}")

    if config.input_format is InputFormat.TXT:
        yield from _iter_txt(config.input_path)
    elif config.input_format is InputFormat.CSV:
        yield from _iter_csv(config.input_path, config.input_column)
    elif config.input_format is InputFormat.JSON:
        yield from _iter_json(config.input_path)
    elif config.input_format is InputFormat.SQLITE:
        yield from _iter_sqlite(config.input_path, config.input_table, config.input_column)
    else:  # pragma: no cover - exhaustive enum, defensive only
        raise LoaderError(f"Unsupported input format: {config.input_format}")


def count_candidates(config: ScanConfig) -> int:
    """Count the total number of candidate URLs without holding them all in memory.

    Used to populate the Rich progress bar's total (and therefore its ETA
    calculation) before a scan starts. Reads the input file a second time
    using the exact same filtering logic as :func:`load_candidates` — this
    guarantees the count can never drift from what will actually be
    processed, at the cost of one extra fast, local, non-network pass over
    a (typically small, text) input file.
    """
    return sum(1 for _ in load_candidates(config))


__all__ = ["LoaderError", "load_candidates", "count_candidates"]
