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


def _split_target_list(raw_target: str) -> tuple[str, ...]:
    """Split a target field into one or more targets on ``;``.

    A target field with no ``;`` yields a one-element tuple -- the same
    shape a single-target override has always had, just under the tuple
    type every caller now uses. Each ``;``-separated piece is stripped;
    empty pieces (blank entries from a stray ``;;`` or leading/trailing
    ``;``) are dropped rather than kept as bogus empty-string "domains."
    If every piece is blank, returns an empty tuple -- callers treat that
    the same as "no override at all" rather than an empty match set.

    Shared by all three loaders (TXT's ``|`` field, CSV's ``target``
    column, JSON's ``"target"`` key) so the three input formats stay
    consistent with each other, as they already are for the
    single-target case.
    """
    return tuple(piece.strip() for piece in raw_target.split(";") if piece.strip())


#: Matches a bare ``account_id`` token: letters/digits/underscore/hyphen
#: only, must start with a letter, no dot and no colon-slash. This is what
#: distinguishes ``account_001|https://...`` (an account-scoped row) from
#: a plain ``https://a.com|target`` row (the pre-existing target-override
#: syntax) -- a real URL always contains ``://`` and/or a ``.``, neither of
#: which this pattern allows, so there is no realistic ambiguity between
#: the two as long as input URLs are written with a scheme (as every input
#: format in this project already requires/assumes).
_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")


def _split_account_prefix(stripped: str) -> tuple[str | None, str]:
    """Split an optional leading ``account_id|`` off a TXT input line.

    Returns ``(account_id, remainder)``. If the line has no ``|``, or its
    first ``|``-delimited segment doesn't look like a bare account-id
    token (see ``_ACCOUNT_ID_PATTERN``), returns ``(None, stripped)``
    unchanged -- so every pre-existing line (with or without its own
    ``|target`` override) parses exactly as it did before this syntax was
    added. Only consumed by ``bl-check``/``bl-chain`` (via
    ``row_metadata["account_id"]``); ``scan``/``crawl`` never look at this
    key, so plain ``scan`` input is unaffected either way.

    Splits on the *first* ``|`` only, matching the same precedent
    ``_split_target_override`` and ``-H "domain.com|Name: Value"`` already
    use -- so ``account_001|https://example.com/a|target`` is read as
    account ``account_001`` plus the remainder ``https://example.com/a|target``,
    which ``_split_target_override`` then splits again for its own
    ``|target`` suffix.
    """
    if "|" not in stripped:
        return None, stripped
    head, _, rest = stripped.partition("|")
    head = head.strip()
    if head and _ACCOUNT_ID_PATTERN.match(head):
        return head, rest
    return None, stripped


def _split_target_override(stripped: str) -> tuple[str, tuple[str, ...]]:
    """Split a TXT line into ``(raw_url, target_overrides)``.

    A row may pin its own target(s) with a ``|`` delimiter --
    ``source_url|target`` or ``source_url|target1;target2;...`` --
    reusing the same delimiter convention ``-H``'s scoped-header syntax
    already uses (``"domain.com|Name: Value"``) rather than inventing a
    new one. Splits on the *first* unescaped ``|`` only (matching that
    same precedent), so a right-hand side that itself happens to contain
    ``|`` is preserved verbatim. A raw, un-percent-encoded ``|`` is not a
    legal character inside a URL per RFC 3986, so there is no realistic
    ambiguity with a URL that contains one.

    A single target (no ``;``) yields a one-element tuple -- unchanged
    from before this field started accepting a list. See
    :func:`_split_target_list` for the ``;`` handling.

    Only consumed by ``bl-check``/``bl-chain`` call sites (via
    ``row_metadata["target"]``) -- ``scan``/``crawl`` never look at this
    key, so a line containing ``|`` is unaffected there.
    """
    if "|" not in stripped:
        return stripped, ()
    url_part, _, target_part = stripped.partition("|")
    url_part = url_part.strip()
    return url_part, _split_target_list(target_part)


def _iter_txt(path: Path) -> Iterator[CandidateURL]:
    """Yield one CandidateURL per non-empty, non-comment line.

    Lines starting with ``#`` (after stripping leading whitespace) are
    treated as comments and skipped, a common convention for plain-text
    URL lists that lets operators annotate or temporarily disable entries.

    A line may optionally pin its own target(s) as ``source_url|target``
    or ``source_url|target1;target2;...`` -- see
    ``_split_target_override``. Lines without ``|`` behave exactly as
    before.

    A line may *also* optionally carry a leading ``account_id|`` prefix
    -- ``account_001|https://example.com/page`` or, combined with a
    target override, ``account_001|https://example.com/page|target`` --
    see ``_split_account_prefix``. This is how ``bl-check``/``bl-chain``
    know which registered session/header set (from ``--accounts-file``)
    to use for that one row; a row without the prefix is unaffected and
    checked as a normal, unauthenticated request. Only consumed by
    ``bl-check``/``bl-chain`` via ``row_metadata["account_id"]`` --
    ``scan``/``crawl`` never look at this key.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                account_id, remainder = _split_account_prefix(stripped)
                url, targets = _split_target_override(remainder)
                if not url:
                    continue
                metadata: dict[str, object] = {}
                if targets:
                    metadata["target"] = targets
                if account_id:
                    metadata["account_id"] = account_id
                yield CandidateURL(raw_url=url, row_metadata=metadata)
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

        # Optional per-row target override column, e.g. for `bl-check`/
        # `bl-chain` input -- recognized case-insensitively like the URL
        # column itself, then normalized into the reserved
        # `row_metadata["target"]` key regardless of the header's own
        # casing (`Target`, `TARGET`, `target` all resolve the same way).
        # The cell may hold one target or a `;`-separated list, same as
        # TXT's `|target` field -- see `_split_target_list`.
        # `scan`/`crawl` never look at this key, so it's inert for them.
        target_col_index: int | None = None
        #: Optional per-row account selector column (see TXT's
        #: ``account_id|`` prefix / JSON's ``"account_id"`` key) -- same
        #: case-insensitive header detection as the ``target`` column.
        account_col_index: int | None = None
        if header:
            header_lower = [h.strip().lower() if h else "" for h in header]
            if "target" in header_lower:
                target_col_index = header_lower.index("target")
            if "account_id" in header_lower:
                account_col_index = header_lower.index("account_id")

        for row in reader:
            if not row or col_index >= len(row):
                continue
            url = row[col_index].strip()
            if not url:
                continue
            if header:
                metadata: dict[str, str | tuple[str, ...]] = {
                    h: v
                    for i, (h, v) in enumerate(zip(header, row, strict=False))
                    if h and i != col_index and i != target_col_index and i != account_col_index
                }
                if target_col_index is not None and target_col_index < len(row):
                    target_value = row[target_col_index].strip()
                    if target_value:
                        targets = _split_target_list(target_value)
                        if targets:
                            metadata["target"] = targets
                if account_col_index is not None and account_col_index < len(row):
                    account_value = row[account_col_index].strip()
                    if account_value:
                        metadata["account_id"] = account_value
                yield CandidateURL(raw_url=url, row_metadata=metadata)
            else:
                yield CandidateURL(raw_url=url)


def _iter_json(path: Path) -> Iterator[CandidateURL]:
    """Yield one CandidateURL per entry of a top-level JSON array.

    Each array element may be either a plain string (the URL itself) or an
    object with a ``url`` key (any other keys are kept as row metadata).
    A ``"target"`` key, if present, is treated the same as TXT's
    ``|target`` field and CSV's ``target`` column -- one target or a
    ``;``-separated list, split via ``_split_target_list`` into a
    ``tuple[str, ...]``. Malformed individual entries are skipped rather
    than aborting the entire load, consistent with this project's
    general policy of never letting one bad row abort a large batch job.
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
                raw_target = metadata.get("target")
                if isinstance(raw_target, str):
                    targets = _split_target_list(raw_target)
                    if targets:
                        metadata["target"] = targets
                    else:
                        del metadata["target"]
                # Optional per-row account selector -- same reserved-key
                # convention as "target", consumed only by bl-check/bl-chain.
                raw_account = metadata.get("account_id")
                if isinstance(raw_account, str):
                    account_id = raw_account.strip()
                    if account_id:
                        metadata["account_id"] = account_id
                    else:
                        del metadata["account_id"]
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
