"""File I/O for the target-replace CLI commands (``redact-target``,
``expand-target``).

Deliberately independent of :class:`~redirecthunter.models.ScanConfig`,
:class:`~redirecthunter.models.CandidateURL`, and
:func:`~redirecthunter.loader.load_candidates` -- those model network-scan
state (retries, workers, timeouts, database rows) that doesn't apply to
this lightweight, plain-text-in / plain-text-or-simple-structured-out
preparation step. This module owns exactly two things: reading a
plain-text, one-URL-per-line input file, and writing the result in
whichever shape each command needs.
"""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TextIO

import orjson

from redirecthunter.models import RedactFormat
from redirecthunter.utils import (
    TARGET_PLACEHOLDER,
    contains_target_placeholder,
    expand_target,
    redact_domain,
)

#: SQLite table name written by the ``redact-target --format sqlite`` writer,
#: matching the retired shell script's ``db`` output shape.
SQLITE_TABLE = "urls"


def read_lines(path: Path) -> Iterator[str]:
    """Yield stripped, non-blank lines from a plain-text file, in order.

    Blank lines are skipped entirely (not written through), matching the
    behavior of the retired ``examples/url-target-replace.sh``.
    """
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n").rstrip("\r")
            if line:
                yield line


def iter_redacted_rows(lines: Iterable[str], domain: str, *, token: str = TARGET_PLACEHOLDER) -> Iterator[tuple[str, str, bool]]:
    """Apply :func:`~redirecthunter.utils.redact_domain` to each line.

    Yields ``(target_url, original_url, matched)`` triples, one per input
    line. ``matched`` is False when no domain occurrence was found, in
    which case ``target_url == original_url`` (fail-open passthrough).
    """
    for original in lines:
        redacted = redact_domain(original, domain, token=token)
        yield redacted, original, redacted != original


def iter_expanded_lines(lines: Iterable[str], target: str | None, *, url_encode: bool = False) -> Iterator[tuple[str, bool]]:
    """Apply :func:`~redirecthunter.utils.expand_target` to each line.

    Yields ``(expanded_line, had_placeholder)`` pairs. ``had_placeholder``
    is False when the line contained no ``{TARGET}`` token, in which case
    the line passes through unchanged.
    """
    for line in lines:
        had_placeholder = contains_target_placeholder(line)
        yield expand_target(line, target, url_encode=url_encode), had_placeholder


def write_txt_rows(rows: Iterable[tuple[str, str, bool]], fh: TextIO) -> int:
    """Write only the ``target_url`` half of each row, one per line."""
    count = 0
    for target_url, _original_url, _matched in rows:
        fh.write(f"{target_url}\n")
        count += 1
    return count


def write_csv_rows(rows: Iterable[tuple[str, str, bool]], fh: TextIO) -> int:
    """Write ``target_url,original_url`` rows, RFC 4180-quoted."""
    writer = csv.writer(fh)
    writer.writerow(("target_url", "original_url"))
    count = 0
    for target_url, original_url, _matched in rows:
        writer.writerow((target_url, original_url))
        count += 1
    return count


def write_json_rows(rows: Iterable[tuple[str, str, bool]], fh: TextIO) -> int:
    """Write a JSON array of ``{"target_url": ..., "original_url": ...}`` objects."""
    payload = [{"target_url": target_url, "original_url": original_url} for target_url, original_url, _matched in rows]
    fh.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode("utf-8"))
    fh.write("\n")
    return len(payload)


def write_sqlite_rows(rows: Iterable[tuple[str, str, bool]], path: Path) -> int:
    """Write rows to a standalone SQLite file: table ``urls`` (target_url, original_url)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    count = 0
    try:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {SQLITE_TABLE} ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  target_url TEXT NOT NULL,"
            "  original_url TEXT NOT NULL"
            ")"
        )
        for target_url, original_url, _matched in rows:
            conn.execute(
                f"INSERT INTO {SQLITE_TABLE} (target_url, original_url) VALUES (?, ?)",
                (target_url, original_url),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


#: Dispatch table from RedactFormat to its text-stream writer. SQLITE is
#: handled separately by callers since it writes to a binary file path,
#: not a text stream.
TEXT_WRITERS = {
    RedactFormat.TXT: write_txt_rows,
    RedactFormat.CSV: write_csv_rows,
    RedactFormat.JSON: write_json_rows,
}


def write_expanded_lines(lines: Iterable[tuple[str, bool]], fh: TextIO) -> int:
    """Write only the expanded line of each ``(line, had_placeholder)`` pair."""
    count = 0
    for expanded, _had_placeholder in lines:
        fh.write(f"{expanded}\n")
        count += 1
    return count


__all__ = [
    "SQLITE_TABLE",
    "TEXT_WRITERS",
    "read_lines",
    "iter_redacted_rows",
    "iter_expanded_lines",
    "write_txt_rows",
    "write_csv_rows",
    "write_json_rows",
    "write_sqlite_rows",
    "write_expanded_lines",
]
