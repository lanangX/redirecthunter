"""RedirectHunter command-line interface.

Wires together every other module into Typer commands:

    - ``scan``           — run a new scan against a candidate-URL input file.
    - ``resume``         — continue an interrupted scan from where it left off.
    - ``stats``          — show aggregate statistics for one or all recorded scans.
    - ``export``         — export a scan's results to CSV, JSON, or SQLite.
    - ``show``           — display individual results in a Rich table, with filters.
    - ``find``           — filter results to redirects outside a given domain.
    - ``delete``         — permanently remove a recorded scan.
    - ``vacuum``         — reclaim disk space freed by deleted scans.
    - ``redact-target``  — replace domain occurrences with a {TARGET} token.
    - ``expand-target``  — expand {TARGET} templates into real URLs.
    - ``crawl``          — crawl a site (or a fixed URL list), auditing broken links and on-page SEO.
    - ``crawl-stats``    — show aggregate statistics for one or all recorded crawls.
    - ``crawl-export``   — export a crawl's pages or checked links to CSV/JSON.
    - ``crawl-show``     — display individual crawled pages or checked links, with filters.
    - ``bl-check``       — check a list of pages for a genuine outbound link to a target domain.
    - ``bl-stats``       — show aggregate statistics for one or all recorded backlink-check runs.
    - ``bl-show``        — display individual per-URL backlink-check results, with filters.
    - ``bl-export``      — export a backlink-check run's results to CSV/JSON.
    - ``bl-chain``        — check a tiered/pyramid backlink structure across multiple tier files.

This module contains no scanning, parsing, or persistence logic of its
own — it only translates CLI flags into a
:class:`~redirecthunter.models.ScanConfig` (or, for the ``crawl*``
commands, a :class:`~redirecthunter.models.CrawlConfig`; for the ``bl-*``
commands, a :class:`~redirecthunter.models.BacklinkCheckConfig`), drives
the :class:`~redirecthunter.engine.Engine` (or
:class:`~redirecthunter.crawler.Crawler`, or
:func:`redirecthunter.backlink.run_backlink_checks`), and renders
progress/output with Rich. The two ``*-target`` commands are the
exception: they're a lightweight, scan-independent preparation step and
delegate their file I/O to :mod:`redirecthunter.target_replace` instead.
``crawl-export``/``bl-export`` are likewise their own small streaming
CSV/JSON writers rather than reusing ``Exporter`` -- see those commands'
docstrings for why. The ``bl-*`` command family also carries
single-letter flag aliases (``-d/--domain``, ``-c/--concurrency``, etc.)
as a deliberate, documented exception to this module's otherwise
long-``--flag``-only convention -- see ``AGENT.md``/``MEMORY.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import re
import sys
from collections.abc import AsyncGenerator, Callable, Iterator, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
import orjson
import typer
from pydantic import ValidationError
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from redirecthunter.backlink import (
    BACKLINK_RESULT_COLUMNS,
    BacklinkResult,
    PlaywrightNotInstalledError,
    normalize_domain,
    run_backlink_checks,
    run_backlink_checks_browser,
)
from redirecthunter.config import (
    ConfigError,
    build_backlink_chain_config,
    build_backlink_check_config,
    build_scan_config,
    infer_input_format,
)
from redirecthunter.crawler import Crawler, CrawlerError
from redirecthunter.database import Database, DatabaseError
from redirecthunter.engine import Engine
from redirecthunter.export import Exporter, ExportError, ExportFilter
from redirecthunter.loader import LoaderError, count_candidates, load_candidates
from redirecthunter.logger import LogLevel, configure_logging, console, get_logger
from redirecthunter.models import (
    BacklinkChainSummary,
    BacklinkCheckConfig,
    BacklinkCheckSummary,
    CandidateURL,
    CrawlConfig,
    CrawlLinkResult,
    CrawlPageResult,
    CrawlSeedMode,
    CrawlSummary,
    ExportFormat,
    HTTPMethod,
    InputFormat,
    RedactFormat,
    RedirectResult,
    RedirectType,
    RunStatus,
    ScanConfig,
    ScanSummary,
)
from redirecthunter.target_replace import (
    TEXT_WRITERS,
    iter_expanded_lines,
    iter_redacted_rows,
    read_lines,
    write_expanded_lines,
    write_sqlite_rows,
)
from redirecthunter.utils import (
    TARGET_PLACEHOLDER,
    format_latency,
    is_external_domain,
    resolve_relative_url,
)

logger = get_logger(__name__)

#: Number of leading characters of a scan_id shown in table listings and
#: suggested to the operator. Purely cosmetic/UX -- the actual resolution
#: of a short scan_id to its full UUID (see Database.resolve_scan_id)
#: accepts any unambiguous prefix, not just exactly this many characters.
SHORT_ID_LENGTH = 8

app = typer.Typer(
    name="redirecthunter",
    help=(
        "HTTP Redirect Discovery and Validation Framework for security auditing, "
        "QA, SEO research, redirect inventory, and migration validation."
    ),
    no_args_is_help=True,
    add_completion=True,
)


def _parse_headers(raw_headers: list[str] | None) -> dict[str, str] | None:
    """Parse repeatable ``--header 'Name: Value'`` CLI options into a dict."""
    if not raw_headers:
        return None
    parsed: dict[str, str] = {}
    for entry in raw_headers:
        if ":" not in entry:
            console.print(
                f"[yellow]Ignoring malformed --header value (expected 'Name: Value'): {entry}[/yellow]"
            )
            continue
        name, _, value = entry.partition(":")
        parsed[name.strip()] = value.strip()
    return parsed


def _parse_account_headers(raw_lines: list[str] | None) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Parse ``--accounts-file`` lines into an ``account_id -> headers`` registry.

    One header per line, ``account_id|Name: Value`` -- the same
    ``left|right`` delimiter convention already used for
    ``-H "domain.com|Name: Value"`` and TXT's ``|target`` row override,
    just keyed by an exact ``account_id`` (see ``loader.py``'s
    ``account_id|URL`` row prefix) instead of a domain. Repeating the
    same ``account_id`` on further lines adds more headers to that same
    account -- this is how one account registers several headers
    (``Cookie``, ``User-Agent``, ``Referer``, ...), not an error. If the
    same ``account_id`` *and* the same header ``Name`` both repeat, the
    later line wins (last-value-wins) -- deliberate, not a bug.

    A bare ``account_id|`` line (nothing after the ``|``) explicitly
    registers that account with zero headers -- distinct from the account
    never appearing in the file at all. This matters for
    `_validate_account_references`: an account explicitly registered with
    no headers is a known, intentional no-op (the row is checked with
    only whatever domain/global headers already apply, same as a public
    row); an account that never appears in the registry at all is a
    configuration error and is refused, not silently treated the same way.

    Returns ``(registry, warnings)`` -- malformed lines are skipped and
    reported back as warnings rather than aborting the whole file, the
    same tolerant style ``_parse_headers`` uses.
    """
    registry: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    if not raw_lines:
        return registry, warnings

    for entry in raw_lines:
        account_id, sep, rest = entry.partition("|")
        account_id = account_id.strip()
        if not sep or not account_id:
            warnings.append(
                f"Ignoring malformed accounts-file line (expected 'account_id|Name: Value'): {entry}"
            )
            continue
        registry.setdefault(account_id, {})
        rest = rest.strip()
        if not rest:
            # Bare "account_id|" -- explicit "this account has no headers".
            continue
        if ":" not in rest:
            warnings.append(
                f"Ignoring malformed accounts-file line (expected 'account_id|Name: Value'): {entry}"
            )
            continue
        name, _, value = rest.partition(":")
        name = name.strip()
        if not name:
            warnings.append(f"Ignoring malformed accounts-file line (empty header name): {entry}")
            continue
        registry[account_id][name] = value.strip()

    return registry, warnings


def _build_per_url_account_ids(candidates: Sequence[CandidateURL]) -> dict[str, str]:
    """Build the per-URL ``account_id`` map from each candidate's ``row_metadata["account_id"]``.

    Only rows that actually carry an ``account_id`` (TXT's ``account_id|``
    prefix, CSV's ``account_id`` column, or JSON's ``"account_id"`` key --
    see ``loader.py``) appear in the returned dict, mirroring
    ``_build_per_url_targets``'s shape/contract exactly. Consumed by
    `resolve_effective_headers`/`run_backlink_checks`'s
    ``per_url_account_id`` parameter; a URL absent from this dict is
    treated as a normal, unauthenticated (no account) request.
    """
    per_url: dict[str, str] = {}
    for candidate in candidates:
        raw_account_id = candidate.row_metadata.get("account_id")
        if isinstance(raw_account_id, str) and raw_account_id.strip():
            per_url[candidate.raw_url] = raw_account_id.strip()
    return per_url


def _validate_account_references(
    per_url_account_id: Mapping[str, str], account_headers: Mapping[str, Mapping[str, str]]
) -> list[str]:
    """Return every distinct ``account_id`` referenced by input rows but absent from the registry.

    Deliberately a hard-error condition, not a silent skip/fallback: a row
    that explicitly asks for ``account_057`` and doesn't get it would
    otherwise be checked as an anonymous request without anyone noticing
    -- for a login-walled page that reads as a false "not found"/"requires
    login" instead of what actually happened (operator typo or missing
    registry entry). Called once, up front, before any request goes out,
    so a typo in account #700 of a 1000-account run is caught immediately
    rather than discovered after the run completes.
    """
    referenced = set(per_url_account_id.values())
    return sorted(referenced - set(account_headers.keys()))


def _read_lines_from_file(path: Path) -> list[str]:
    """Read a `bl-check --accounts-file`: one ``account_id|Name: Value`` line per line.

    Same tolerant, `#`-comment/blank-line-skipping convention as the URL
    input loader (`examples/urls.txt`), parsed by `_parse_account_headers`.
    """
    lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


#: Matches a bare 3-digit HTTP status code, e.g. "301" or "404".
_STATUS_CODE_RE = re.compile(r"^\d{3}$")
#: Matches a status "class" shorthand, e.g. "3xx" or "4XX", meaning "any
#: code whose first digit is 3" / "...is 4".
_STATUS_CLASS_RE = re.compile(r"^([1-5])xx$", re.IGNORECASE)


def _parse_status_filter(raw: list[str] | None) -> tuple[frozenset[int], frozenset[int]]:
    """Parse repeatable ``--status-code`` values into exact codes + classes.

    Each ``--status-code`` value may itself be a comma-separated list, and
    each entry is either an exact 3-digit code (``301``, ``404``) or a
    class shorthand (``3xx``, ``4xx``) meaning "any code starting with
    that digit". Malformed entries are reported and skipped rather than
    aborting the command, matching ``_parse_headers``'s tolerant style.
    """
    codes: set[int] = set()
    classes: set[int] = set()
    if not raw:
        return frozenset(codes), frozenset(classes)
    for group in raw:
        for token in group.split(","):
            token = token.strip()
            if not token:
                continue
            if _STATUS_CODE_RE.match(token):
                codes.add(int(token))
                continue
            class_match = _STATUS_CLASS_RE.match(token)
            if class_match:
                classes.add(int(class_match.group(1)))
                continue
            console.print(
                f"[yellow]Ignoring malformed --status-code value (expected e.g. '301' or "
                f"'3xx'): {token}[/yellow]"
            )
    return frozenset(codes), frozenset(classes)


def _build_progress() -> Progress:
    """Construct the standard Rich progress display used by scan/resume.

    Columns: spinner, description, bar, N/total count, elapsed, ETA — the
    "workers / speed / ETA / success / failures" live-counter requirement
    is satisfied by the dynamically-updated description text (see
    ``_progress_description``) plus these fixed columns.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("ETA:"),
        TimeRemainingColumn(),
        console=console,
    )


def _progress_description(engine: Engine, workers: int) -> str:
    """Build the dynamic status line: workers, throughput, success/failure counts."""
    stats = engine.stats
    return (
        f"[bold cyan]RedirectHunter[/bold cyan] "
        f"workers={workers} "
        f"[green]alive={stats.alive}[/green] "
        f"[red]dead={stats.dead}[/red] "
        f"redirects={stats.redirects} "
        f"[dim]{stats.requests_per_second:.1f} url/s[/dim]"
    )


def _crawl_progress_description(crawler: Crawler, workers: int) -> str:
    """Build the dynamic status line for a running crawl. Mirrors ``_progress_description``."""
    stats = crawler.stats
    return (
        f"[bold cyan]RedirectHunter Crawl[/bold cyan] "
        f"workers={workers} "
        f"[green]alive={stats.pages_alive}[/green] "
        f"[red]dead={stats.pages_dead}[/red] "
        f"links={stats.links_checked} "
        f"[red]broken={stats.links_broken}[/red] "
        f"[dim]{stats.pages_per_second:.1f} pages/s[/dim]"
    )


async def _resolve_crawl_seeds(
    *,
    seed: str | None,
    input_file: Path | None,
    input_format: InputFormat | None,
    input_column: str,
) -> tuple[CrawlSeedMode, list[str], InputFormat | None]:
    """Resolve a crawl's starting URL(s) from either a single seed or an input file.

    Reuses :func:`redirecthunter.loader.load_candidates` for the
    ``URL_LIST`` case rather than re-implementing TXT/CSV/JSON parsing --
    a crawl seed file is read exactly the same way a ``scan`` input file
    is, so building a throwaway ``ScanConfig`` just to drive the existing
    loader is simpler and safer than duplicating its four format parsers.
    """
    if input_file is not None:
        resolved_format = input_format or infer_input_format(input_file)
        loader_config = ScanConfig(
            input_path=input_file, input_format=resolved_format, input_column=input_column
        )
        seeds = [candidate.raw_url for candidate in load_candidates(loader_config)]
        return CrawlSeedMode.URL_LIST, seeds, resolved_format

    if seed is not None:
        return CrawlSeedMode.DOMAIN, [seed], None

    console.print("[bold red]Error:[/bold red] Provide a seed URL argument or --input-file.")
    raise typer.Exit(code=1)


async def _run_crawl(config: CrawlConfig, seeds: list[str]) -> None:
    """Execution path for the ``crawl`` command: run a Crawler, persisting pages/links as they complete."""
    database = Database(config.database_path)
    await database.connect()

    summary: CrawlSummary | None = None
    try:
        await database.create_crawl(config)
        console.print(
            f"Starting crawl [bold]{config.crawl_id}[/bold] "
            f"(seed_mode={config.seed_mode.value}, {len(seeds)} seed(s), "
            f"max_pages={config.max_pages}, max_depth={config.max_depth}, workers={config.workers})"
        )
        if not config.follow_links:
            console.print(
                "[yellow]Note:[/yellow] --no-follow-links is set -- only the seed URL(s) "
                "themselves will be crawled as pages; their links are still checked for "
                "broken status but never expanded into further pages."
            )

        crawler = Crawler(config)
        progress = _build_progress()
        with progress:
            task_id = progress.add_task(
                _crawl_progress_description(crawler, config.workers), total=config.max_pages
            )

            async def on_page(page: CrawlPageResult) -> None:
                await database.save_crawl_page(page)
                progress.update(
                    task_id,
                    completed=crawler.stats.pages_completed,
                    description=_crawl_progress_description(crawler, config.workers),
                )

            async def on_link(link: CrawlLinkResult) -> None:
                await database.save_crawl_link(link)
                progress.update(task_id, description=_crawl_progress_description(crawler, config.workers))

            try:
                await crawler.run(seeds, on_page, on_link)
                await database.update_crawl_status(config.crawl_id, RunStatus.COMPLETED, finished=True)
            except CrawlerError as exc:
                await database.update_crawl_status(config.crawl_id, RunStatus.FAILED, finished=True)
                console.print(f"[bold red]Crawl error:[/bold red] {exc}")
                raise typer.Exit(code=1) from None
            except (KeyboardInterrupt, asyncio.CancelledError):
                await database.update_crawl_status(config.crawl_id, RunStatus.INTERRUPTED, finished=True)
                console.print("\n[yellow]Crawl interrupted.[/yellow]")
                raise
            except Exception:
                await database.update_crawl_status(config.crawl_id, RunStatus.FAILED, finished=True)
                raise

        summary = await database.get_crawl_summary(config.crawl_id)
    finally:
        await database.close()

    if summary is not None:
        _print_crawl_summary_table(summary)


def _print_crawl_summary_table(summary: CrawlSummary) -> None:
    """Render a CrawlSummary as a two-column Rich table. Mirrors ``_print_summary_table``."""
    table = Table(title=f"Crawl Summary: {summary.crawl_id[:SHORT_ID_LENGTH]}", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Label", summary.label or "-")
    table.add_row("Status", summary.status.value)
    table.add_row("Seed mode", summary.seed_mode.value)
    table.add_row("Pages crawled", str(summary.pages_crawled))
    table.add_row("Pages alive", f"[green]{summary.pages_alive}[/green]")
    table.add_row("Pages dead", f"[red]{summary.pages_dead}[/red]" if summary.pages_dead else "0")
    table.add_row("Links checked", str(summary.links_checked))
    table.add_row(
        "Broken links",
        f"[red]{summary.broken_links}[/red] "
        f"({summary.broken_internal_links} internal, {summary.broken_external_links} external)"
        if summary.broken_links
        else "0",
    )
    table.add_row("Missing title", str(summary.pages_missing_title))
    table.add_row("Duplicate title", str(summary.pages_duplicate_title))
    table.add_row("Missing meta description", str(summary.pages_missing_meta_description))
    table.add_row("Duplicate meta description", str(summary.pages_duplicate_meta_description))
    table.add_row("Missing H1", str(summary.pages_missing_h1))
    table.add_row("Multiple H1", str(summary.pages_multiple_h1))
    table.add_row("Avg page latency", format_latency(summary.avg_latency_ms))
    table.add_row("Started", str(summary.started_at) if summary.started_at else "-")
    table.add_row("Finished", str(summary.finished_at) if summary.finished_at else "-")
    console.print(table)


async def _run_scan(config: ScanConfig, *, resume: bool) -> None:
    """Shared execution path for both ``scan`` and ``resume``.

    Args:
        config: The fully-resolved ScanConfig.
        resume: If True, an existing scan row must already exist for
            ``config.scan_id`` and already-completed URLs are skipped.
    """
    try:
        total = count_candidates(config)
    except LoaderError as exc:
        console.print(f"[bold red]Input error:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    if total == 0:
        console.print("[yellow]No candidate URLs found in the input file. Nothing to do.[/yellow]")
        raise typer.Exit(code=0)

    database = Database(config.database_path)
    await database.connect()

    already_completed: set[str] = set()
    summary = None
    try:
        if resume:
            if not await database.scan_exists(config.scan_id):
                console.print(f"[bold red]No such scan to resume:[/bold red] {config.scan_id}")
                raise typer.Exit(code=1)
            already_completed = await database.get_completed_source_urls(config.scan_id)
            console.print(
                f"Resuming scan [bold]{config.scan_id}[/bold]: "
                f"{len(already_completed)}/{total} already completed."
            )
        else:
            await database.create_scan(config, total_urls=total)
            console.print(
                f"Starting scan [bold]{config.scan_id}[/bold] "
                f"({total} candidate URLs, {config.workers} workers, method={config.method.value})"
            )
            if config.method is HTTPMethod.HEAD:
                console.print(
                    "[yellow]Note:[/yellow] method=HEAD has no response body, so meta-refresh, "
                    "JavaScript-redirect, and body-link (--has-link-only) detection will all be "
                    "skipped for this scan. Re-run with [bold]--method GET[/bold] if you need any "
                    "of those."
                )

        def remaining_candidates() -> Iterator[CandidateURL]:
            for candidate in load_candidates(config):
                if candidate.raw_url not in already_completed:
                    yield candidate

        remaining_total = total - len(already_completed)
        engine = Engine(config)

        progress = _build_progress()
        with progress:
            task_id = progress.add_task(
                _progress_description(engine, config.workers), total=remaining_total
            )

            async def on_result(result: RedirectResult, terminal_headers: httpx.Headers | None) -> None:
                await database.save_result(
                    result, final_headers=dict(terminal_headers) if terminal_headers else None
                )
                progress.update(
                    task_id, advance=1, description=_progress_description(engine, config.workers)
                )

            try:
                await engine.run(remaining_candidates(), on_result, total=remaining_total)
                await database.update_scan_status(config.scan_id, RunStatus.COMPLETED, finished=True)
            except (KeyboardInterrupt, asyncio.CancelledError):
                await database.update_scan_status(config.scan_id, RunStatus.INTERRUPTED)
                console.print(
                    f"\n[yellow]Scan interrupted.[/yellow] Resume with: "
                    f"redirecthunter resume {config.scan_id} --database {config.database_path}"
                )
                raise
            except Exception:
                await database.update_scan_status(config.scan_id, RunStatus.FAILED, finished=True)
                raise

        summary = await database.get_scan_summary(config.scan_id)
    finally:
        await database.close()

    if summary is not None:
        _print_summary_table(summary)


def _print_summary_table(summary: ScanSummary) -> None:
    """Render a scan's final summary as a Rich table."""
    table = Table(title=f"Scan Summary — {summary.scan_id}", show_header=False)
    table.add_row("Label", summary.label or "-")
    table.add_row("Status", summary.status.value)
    table.add_row("Total URLs", str(summary.total_urls))
    table.add_row("Completed", f"{summary.completed} ({summary.progress_pct}%)")
    table.add_row("Alive", f"[green]{summary.alive}[/green]")
    table.add_row("Dead", f"[red]{summary.dead}[/red]")
    table.add_row("Redirects Found", str(summary.redirects_found))
    table.add_row("Cloudflare Protected", str(summary.cloudflare_protected))
    table.add_row("Avg Latency", f"{summary.avg_latency_ms:.1f} ms")
    console.print(table)


# --------------------------------------------------------------------------
# Shared option definitions (used by both `scan` and `resume`)
# --------------------------------------------------------------------------

_TargetOption = Annotated[
    str | None,
    typer.Option("--target", help="Replacement value for {TARGET} in candidate URL templates."),
]
_MethodOption = Annotated[
    HTTPMethod | None,
    typer.Option(
        "--method",
        case_sensitive=False,
        help="HTTP method (HEAD or GET). GET is required to detect meta-refresh/JS redirects "
        "and to populate body_link (--has-link-only) -- HEAD requests never have a body.",
    ),
]
_WorkersOption = Annotated[
    int | None, typer.Option("--workers", min=1, max=2000, help="Concurrent worker count. Default: 100.")
]
_TimeoutOption = Annotated[
    float | None, typer.Option("--timeout", help="Per-request total timeout in seconds. Default: 10.")
]
_ConnectTimeoutOption = Annotated[
    float | None,
    typer.Option("--connect-timeout", help="Per-request connect timeout in seconds. Default: 5."),
]
_RetryOption = Annotated[
    int | None, typer.Option("--retry", help="Number of retries on transport-level failure. Default: 2.")
]
_RetryBackoffOption = Annotated[
    float | None,
    typer.Option("--retry-backoff", help="Base exponential backoff delay in seconds. Default: 0.5."),
]
_RateLimitOption = Annotated[
    float | None,
    typer.Option(
        "--rate-limit", help="Max aggregate requests/second across all workers. Default: unlimited."
    ),
]
_MaxRedirectsOption = Annotated[
    int | None, typer.Option("--max-redirects", help="Maximum redirects to follow per URL. Default: 10.")
]
_FollowRedirectsOption = Annotated[
    bool | None,
    typer.Option(
        "--follow-redirects/--no-follow-redirects",
        help="Follow the full redirect chain, or inspect only the first hop.",
    ),
]
_Http2Option = Annotated[bool | None, typer.Option("--http2/--no-http2", help="Enable HTTP/2. Default: enabled.")]
_ProxyOption = Annotated[
    str | None,
    typer.Option("--proxy", help="Proxy URL, e.g. socks5://127.0.0.1:1080 or http://proxy:8080."),
]
_UserAgentOption = Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent header.")]
_HeaderOption = Annotated[
    list[str] | None, typer.Option("--header", "-H", help="Extra header as 'Name: Value'. Repeatable.")
]
_VerifyTlsOption = Annotated[
    bool | None, typer.Option("--verify-tls/--insecure", help="Verify TLS certificates. Default: enabled.")
]
_ConfigFileOption = Annotated[
    Path | None,
    typer.Option("--config", exists=True, dir_okay=False, help="YAML config file. Auto-discovered if omitted."),
]
_LabelOption = Annotated[str | None, typer.Option("--label", help="Human-readable label for this scan.")]
_LogFileOption = Annotated[Path | None, typer.Option("--log-file", help="Also write logs to this file.")]


@app.command()
def scan(
    input_file: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="Candidate URL input file (.txt, .csv, .json, .db/.sqlite)."),
    ],
    format: Annotated[  # noqa: A002 - CLI flag name intentionally shadows the builtin
        InputFormat | None,
        typer.Option("--format", case_sensitive=False, help="Input file format. Inferred from extension if omitted."),
    ] = None,
    input_table: Annotated[
        str | None, typer.Option("--input-table", help="Table name for SQLite input. Default: 'urls'.")
    ] = None,
    input_column: Annotated[
        str | None, typer.Option("--input-column", help="URL column name for CSV/SQLite input. Default: 'url'.")
    ] = None,
    target: _TargetOption = None,
    method: _MethodOption = None,
    workers: _WorkersOption = None,
    timeout: _TimeoutOption = None,
    connect_timeout: _ConnectTimeoutOption = None,
    retry: _RetryOption = None,
    retry_backoff: _RetryBackoffOption = None,
    rate_limit: _RateLimitOption = None,
    max_redirects: _MaxRedirectsOption = None,
    follow_redirects: _FollowRedirectsOption = None,
    http2: _Http2Option = None,
    proxy: _ProxyOption = None,
    user_agent: _UserAgentOption = None,
    header: _HeaderOption = None,
    verify_tls: _VerifyTlsOption = None,
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
    config_file: _ConfigFileOption = None,
    label: _LabelOption = None,
    log_level: Annotated[LogLevel, typer.Option("--log-level", case_sensitive=False, help="Logging verbosity.")] = LogLevel.INFO,
    log_file: _LogFileOption = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress console log output.")] = False,
) -> None:
    """Run a new redirect-discovery scan against a candidate URL input file.

    Examples:

        redirecthunter scan urls.txt

        redirecthunter scan urls.txt --target https://example.org

        redirecthunter scan urls.txt --workers 500 --method GET

        redirecthunter scan urls.txt --proxy socks5://127.0.0.1:1080
    """
    configure_logging(log_level, log_file, quiet=quiet)

    try:
        resolved_config = build_scan_config(
            input_path=input_file,
            input_format=format,
            input_table=input_table,
            input_column=input_column,
            target=target,
            method=method,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            workers=workers,
            timeout=timeout,
            connect_timeout=connect_timeout,
            retry=retry,
            retry_backoff=retry_backoff,
            rate_limit=rate_limit,
            http2=http2,
            proxy=proxy,
            user_agent=user_agent,
            extra_headers=_parse_headers(header),
            verify_tls=verify_tls,
            database_path=database,
            scan_label=label,
            config_file=config_file,
        )
    except ConfigError as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    asyncio.run(_run_scan(resolved_config, resume=False))


@app.command()
def resume(
    scan_id: Annotated[str, typer.Argument(help="The scan_id of a previously interrupted scan.")],
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
    workers: _WorkersOption = None,
    timeout: _TimeoutOption = None,
    rate_limit: _RateLimitOption = None,
    log_level: Annotated[LogLevel, typer.Option("--log-level", case_sensitive=False, help="Logging verbosity.")] = LogLevel.INFO,
    log_file: _LogFileOption = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress console log output.")] = False,
) -> None:
    """Resume a previously interrupted scan from where it left off.

    Rebuilds the exact configuration (input file, target, method, headers,
    etc.) used by the original ``scan`` invocation, skipping every
    candidate URL that already has a recorded result. A subset of
    performance-related flags (workers, timeout, rate-limit) can be
    overridden for the resumed run.

    Example:

        redirecthunter resume 3f9a1c2e-... --database redirecthunter.db
    """
    configure_logging(log_level, log_file, quiet=quiet)

    async def _load_and_run() -> None:
        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_scan_id = await db.resolve_scan_id(scan_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None
            original_config = await db.get_scan_config(resolved_scan_id)
        finally:
            await db.close()

        if original_config is None:
            console.print(f"[bold red]No such scan in {database}:[/bold red] {scan_id}")
            raise typer.Exit(code=1)

        overrides: dict[str, object] = {"database_path": database}
        if workers is not None:
            overrides["workers"] = workers
        if timeout is not None:
            overrides["timeout"] = timeout
        if rate_limit is not None:
            overrides["rate_limit"] = rate_limit

        resolved_config = original_config.model_copy(update=overrides) if overrides else original_config
        await _run_scan(resolved_config, resume=True)

    asyncio.run(_load_and_run())


#: Column order for `crawl-export --type pages`. Mirrors `export/csv_writer.py`'s
#: CSV_COLUMNS convention: nested structures (H1 list, issue list) are
#: summarized as delimited strings rather than exploded into sparse columns.
_CRAWL_PAGE_COLUMNS: tuple[str, ...] = (
    "page_id",
    "crawl_id",
    "url",
    "depth",
    "discovered_from",
    "status_code",
    "alive",
    "redirected",
    "final_url",
    "content_type",
    "title",
    "title_length",
    "meta_description",
    "meta_description_length",
    "h1_count",
    "h1_texts",
    "internal_link_count",
    "external_link_count",
    "word_count",
    "issues",
    "latency_ms",
    "error",
    "timestamp",
)

#: Column order for `crawl-export --type links`.
_CRAWL_LINK_COLUMNS: tuple[str, ...] = (
    "link_id",
    "crawl_id",
    "source_page_url",
    "target_url",
    "raw_href",
    "link_kind",
    "anchor_text",
    "rel",
    "target_attr",
    "status_code",
    "is_broken",
    "redirected",
    "final_url",
    "error",
    "latency_ms",
    "checked_at",
)


def _page_to_row(page: CrawlPageResult) -> list[str | int | float]:
    """Flatten one CrawlPageResult into a CSV/JSON row matching _CRAWL_PAGE_COLUMNS."""
    return [
        page.page_id,
        page.crawl_id,
        page.url,
        page.depth,
        page.discovered_from or "",
        page.status_code if page.status_code is not None else "",
        "yes" if page.alive else "no",
        "yes" if page.redirected else "no",
        page.final_url or "",
        page.content_type or "",
        page.title or "",
        page.title_length,
        page.meta_description or "",
        page.meta_description_length,
        page.h1_count,
        " | ".join(page.h1_texts),
        page.internal_link_count,
        page.external_link_count,
        page.word_count,
        ", ".join(issue.value for issue in page.issues),
        round(page.latency_ms, 2),
        page.error or "",
        page.timestamp.isoformat(),
    ]


def _link_to_row(link: CrawlLinkResult) -> list[str | int | float]:
    """Flatten one CrawlLinkResult into a CSV/JSON row matching _CRAWL_LINK_COLUMNS."""
    return [
        link.link_id,
        link.crawl_id,
        link.source_page_url,
        link.target_url,
        link.raw_href,
        link.link_kind.value,
        link.anchor_text or "",
        link.rel or "",
        link.target_attr or "",
        link.status_code if link.status_code is not None else "",
        "yes" if link.is_broken else "no",
        "yes" if link.redirected else "no",
        link.final_url or "",
        link.error or "",
        round(link.latency_ms, 2),
        link.checked_at.isoformat(),
    ]


async def _write_crawl_export(
    rows: AsyncGenerator[CrawlPageResult] | AsyncGenerator[CrawlLinkResult],
    output_path: Path,
    fmt: ExportFormat,
    columns: tuple[str, ...],
    to_row: Callable[[Any], list[str | int | float]],
) -> int:
    """Stream a crawl-export row source to CSV or JSON at ``output_path``.

    A small, deliberately generic streaming writer shared by both
    ``--type pages`` and ``--type links`` -- the two only differ in which
    async generator and row-flattening function they pass in, so a single
    writer (rather than two near-duplicate ones) avoids the format-writing
    logic itself drifting between them.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    if fmt is ExportFormat.CSV:
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            async for row in rows:
                writer.writerow(to_row(row))
                count += 1
    else:  # ExportFormat.JSON
        with output_path.open("wb") as fh:
            fh.write(b"[")
            first = True
            async for row in rows:
                record = dict(zip(columns, to_row(row), strict=True))
                if not first:
                    fh.write(b",")
                fh.write(orjson.dumps(record))
                first = False
                count += 1
            fh.write(b"]")
    return count


@app.command()
def crawl(
    seed: Annotated[
        str | None,
        typer.Argument(
            help="Seed URL to crawl, e.g. https://example.com. Omit if using --input-file instead."
        ),
    ] = None,
    input_file: Annotated[
        Path | None,
        typer.Option(
            "--input-file",
            exists=True,
            dir_okay=False,
            help="Seed the crawl from every URL in this file (TXT/CSV/JSON) instead of one domain. "
            "Each row is crawled/audited as its own page; use --no-follow-links to check only "
            "those exact pages without discovering more.",
        ),
    ] = None,
    format: Annotated[  # noqa: A002
        InputFormat | None,
        typer.Option("--format", case_sensitive=False, help="--input-file format. Inferred from extension if omitted."),
    ] = None,
    input_column: Annotated[
        str, typer.Option("--input-column", help="URL column name for CSV input. Default: 'url'.")
    ] = "url",
    allowed_domain: Annotated[
        list[str] | None,
        typer.Option(
            "--allowed-domain",
            help="Extra hostname treated as internal scope, besides the seed's own host "
            "(repeatable, e.g. --allowed-domain blog.example.com).",
        ),
    ] = None,
    max_depth: Annotated[
        int, typer.Option("--max-depth", help="Maximum link-hops from a seed to still crawl as a page.")
    ] = 3,
    max_pages: Annotated[
        int, typer.Option("--max-pages", help="Maximum number of pages to fetch and fully audit.")
    ] = 500,
    follow_links: Annotated[
        bool,
        typer.Option(
            "--follow-links/--no-follow-links",
            help="Discover and crawl further pages from links found on each page. Disable to "
            "only audit the given seed(s), still checking (but not crawling) their links.",
        ),
    ] = True,
    check_external_links: Annotated[
        bool,
        typer.Option(
            "--check-external-links/--no-check-external-links",
            help="Request external links to detect broken ones. Disable to only count them.",
        ),
    ] = True,
    include_query_string: Annotated[
        bool,
        typer.Option(
            "--include-query-string/--no-include-query-string",
            help="Treat URLs differing only by query string as different pages. Disable to "
            "collapse e.g. /products?sort=price and /products?sort=name into one page.",
        ),
    ] = True,
    workers: Annotated[int, typer.Option("--workers", help="Concurrent crawl workers.")] = 20,
    timeout: Annotated[float, typer.Option("--timeout", help="Total request timeout, seconds.")] = 10.0,
    connect_timeout: Annotated[
        float, typer.Option("--connect-timeout", help="Connection timeout, seconds.")
    ] = 5.0,
    retry: Annotated[int, typer.Option("--retry", help="Retries for transport-level failures.")] = 1,
    retry_backoff: Annotated[
        float, typer.Option("--retry-backoff", help="Base retry backoff, seconds (exponential).")
    ] = 0.5,
    rate_limit: Annotated[
        float | None, typer.Option("--rate-limit", help="Max requests/second across all workers.")
    ] = None,
    http2: Annotated[bool, typer.Option("--http2/--no-http2", help="Enable HTTP/2. Default: enabled.")] = True,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL, e.g. socks5://127.0.0.1:1080.")] = None,
    user_agent: Annotated[str, typer.Option("--user-agent", help="Custom User-Agent header.")] = (
        "RedirectHunter-Crawler/1.0"
    ),
    header: Annotated[
        list[str] | None, typer.Option("--header", help="Extra request header 'Name: Value' (repeatable).")
    ] = None,
    verify_tls: Annotated[bool, typer.Option("--verify-tls/--no-verify-tls", help="Verify TLS certificates.")] = True,
    title_min_length: Annotated[
        int, typer.Option("--title-min-length", help="Titles shorter than this are flagged 'too short'.")
    ] = 10,
    title_max_length: Annotated[
        int, typer.Option("--title-max-length", help="Titles longer than this are flagged 'too long'.")
    ] = 60,
    meta_description_max_length: Annotated[
        int,
        typer.Option(
            "--meta-description-max-length", help="Meta descriptions longer than this are flagged."
        ),
    ] = 160,
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
    label: Annotated[str | None, typer.Option("--label", help="Human-readable label for this crawl.")] = None,
    log_level: Annotated[LogLevel, typer.Option("--log-level", case_sensitive=False, help="Logging verbosity.")] = LogLevel.INFO,
    log_file: Annotated[Path | None, typer.Option("--log-file", help="Also write logs to this file.")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress console log output.")] = False,
) -> None:
    """Crawl a site (or a fixed list of pages) discovering broken links and on-page SEO issues.

    Two seed modes, chosen by which argument you pass:

        - A seed URL (positional): discovers pages by following internal
          links outward from it, like an Ahrefs-style site audit.
        - ``--input-file``: audits exactly the URLs in that file (same
          TXT/CSV/JSON formats as `scan`'s input file), optionally still
          discovering further pages from them unless
          ``--no-follow-links`` is set.

    Every internal page found is fetched and checked for title/meta
    description/H1 issues; every link found (internal or external) is
    checked for broken (4xx/5xx/transport-error) status. Results land in
    the same SQLite database as `scan` (separate tables) -- see
    `crawl-stats`, `crawl-export`, and `crawl-show`.

    Examples:

        redirecthunter crawl https://example.com

        redirecthunter crawl https://example.com --max-depth 2 --max-pages 200

        redirecthunter crawl --input-file urls.txt --no-follow-links

        redirecthunter crawl https://example.com --no-check-external-links --workers 50
    """
    configure_logging(log_level, log_file, quiet=quiet)

    async def _prepare_and_run() -> None:
        seed_mode, seeds, resolved_format = await _resolve_crawl_seeds(
            seed=seed, input_file=input_file, input_format=format, input_column=input_column
        )
        if not seeds:
            console.print("[yellow]No seed URLs found. Nothing to do.[/yellow]")
            raise typer.Exit(code=0)

        try:
            resolved_config = CrawlConfig(
                crawl_label=label,
                seed_mode=seed_mode,
                seed_url=seeds[0] if seed_mode is CrawlSeedMode.DOMAIN else None,
                seed_input_path=input_file,
                seed_input_format=resolved_format,
                seed_input_column=input_column,
                allowed_domains=allowed_domain or [],
                max_depth=max_depth,
                max_pages=max_pages,
                follow_links=follow_links,
                check_external_links=check_external_links,
                include_query_string=include_query_string,
                workers=workers,
                timeout=timeout,
                connect_timeout=connect_timeout,
                retry=retry,
                retry_backoff=retry_backoff,
                rate_limit=rate_limit,
                http2=http2,
                proxy=proxy,
                user_agent=user_agent,
                extra_headers=_parse_headers(header) or {},
                verify_tls=verify_tls,
                title_min_length=title_min_length,
                title_max_length=title_max_length,
                meta_description_max_length=meta_description_max_length,
                database_path=database,
            )
        except ValidationError as exc:
            console.print(f"[bold red]Configuration error:[/bold red] {exc}")
            raise typer.Exit(code=1) from None

        await _run_crawl(resolved_config, seeds)

    asyncio.run(_prepare_and_run())


@app.command(name="crawl-stats")
def crawl_stats(
    crawl_id: Annotated[
        str | None, typer.Argument(help="Crawl ID (full or a short prefix). Omit to list all crawls.")
    ] = None,
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
) -> None:
    """Show aggregate statistics for one crawl, or list all recorded crawls. Mirrors `stats`.

    Examples:

        redirecthunter crawl-stats

        redirecthunter crawl-stats 3f9a1c2e
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    async def _show_crawl_stats() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            if crawl_id is not None:
                try:
                    resolved_crawl_id = await db.resolve_crawl_id(crawl_id)
                except DatabaseError as exc:
                    console.print(f"[bold red]{exc}[/bold red]")
                    raise typer.Exit(code=1) from None
                summary = await db.get_crawl_summary(resolved_crawl_id)
                if summary is None:
                    console.print(f"[bold red]No such crawl:[/bold red] {crawl_id}")
                    raise typer.Exit(code=1)
                _print_crawl_summary_table(summary)
            else:
                summaries = await db.list_crawls()
                if not summaries:
                    console.print("[yellow]No crawls recorded in this database yet.[/yellow]")
                    return
                table = Table(title=f"Crawls in {database}")
                for column in ("Crawl ID", "Label", "Status", "Pages", "Links", "Broken links", "Missing title"):
                    table.add_column(column)
                for s in summaries:
                    table.add_row(
                        s.crawl_id[:SHORT_ID_LENGTH],
                        s.label or "-",
                        s.status.value,
                        f"[green]{s.pages_alive}[/green]/{s.pages_crawled}",
                        str(s.links_checked),
                        f"[red]{s.broken_links}[/red]" if s.broken_links else "0",
                        str(s.pages_missing_title),
                    )
                console.print(table)
                console.print(
                    "[dim]Tip: the shortened Crawl ID above works directly with other commands, "
                    "e.g. `redirecthunter crawl-show " + summaries[0].crawl_id[:SHORT_ID_LENGTH] + "`.[/dim]"
                )
        finally:
            await db.close()

    asyncio.run(_show_crawl_stats())


@app.command(name="crawl-export")
def crawl_export(
    crawl_id: Annotated[str, typer.Argument(help="Crawl ID (full or a short prefix).")],
    output: Annotated[Path, typer.Argument(help="Output file path.")],
    type: Annotated[  # noqa: A002 - CLI flag name intentionally shadows the builtin
        str,
        typer.Option("--type", help="What to export: 'pages' (on-page SEO audit) or 'links' (broken-link report)."),
    ] = "pages",
    format: Annotated[  # noqa: A002
        ExportFormat,
        typer.Option("--format", case_sensitive=False, help="Export format (csv or json; sqlite is not supported)."),
    ] = ExportFormat.CSV,
    broken_only: Annotated[
        bool,
        typer.Option(
            "--broken-only",
            help="For --type links: only broken links. For --type pages: only pages that link "
            "to at least one broken URL.",
        ),
    ] = False,
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
) -> None:
    """Export a crawl's pages or links to CSV or JSON.

    Deliberately its own small streaming writer (not `Exporter`, which is
    ``RedirectResult``-shaped): a crawl page and a checked link have
    different columns entirely, and both simply stream straight from
    `Database.iter_crawl_pages` / `iter_crawl_links` with no per-row
    transformation `ExportFilter` would otherwise provide.

    Examples:

        redirecthunter crawl-export 3f9a1c2e pages.csv

        redirecthunter crawl-export 3f9a1c2e broken_links.csv --type links --broken-only

        redirecthunter crawl-export 3f9a1c2e pages.json --format json
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    if type not in ("pages", "links"):
        console.print("[bold red]Error:[/bold red] --type must be 'pages' or 'links'.")
        raise typer.Exit(code=1)
    if format is ExportFormat.SQLITE:
        console.print("[bold red]Error:[/bold red] --format sqlite is not supported for crawl-export.")
        raise typer.Exit(code=1)

    async def _do_crawl_export() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_crawl_id = await db.resolve_crawl_id(crawl_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            output.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with console.status(f"Exporting crawl {resolved_crawl_id} {type} to {output} ({format.value})..."):
                if type == "pages":
                    page_rows = db.iter_crawl_pages(resolved_crawl_id, broken_links_only=broken_only)
                    count = await _write_crawl_export(page_rows, output, format, _CRAWL_PAGE_COLUMNS, _page_to_row)
                else:
                    link_rows = db.iter_crawl_links(resolved_crawl_id, broken_only=broken_only)
                    count = await _write_crawl_export(link_rows, output, format, _CRAWL_LINK_COLUMNS, _link_to_row)
            console.print(f"[bold green]Exported {count} {type}[/bold green] to {output}")
        finally:
            await db.close()

    asyncio.run(_do_crawl_export())


@app.command(name="crawl-show")
def crawl_show(
    crawl_id: Annotated[str, typer.Argument(help="Crawl ID (full or a short prefix).")],
    type: Annotated[  # noqa: A002
        str, typer.Option("--type", help="What to show: 'pages' or 'links'.")
    ] = "pages",
    broken_only: Annotated[
        bool, typer.Option("--broken-only", help="For --type links, only show broken links.")
    ] = False,
    issues_only: Annotated[
        bool, typer.Option("--issues-only", help="For --type pages, only show pages with at least one issue.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Maximum rows to display.")] = 50,
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
) -> None:
    """Display individual crawled pages or checked links in a Rich table. Mirrors `show`.

    Examples:

        redirecthunter crawl-show 3f9a1c2e

        redirecthunter crawl-show 3f9a1c2e --issues-only

        redirecthunter crawl-show 3f9a1c2e --type links --broken-only
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    if type not in ("pages", "links"):
        console.print("[bold red]Error:[/bold red] --type must be 'pages' or 'links'.")
        raise typer.Exit(code=1)

    async def _do_show() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_crawl_id = await db.resolve_crawl_id(crawl_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            if type == "pages":
                table = Table(title=f"Pages for crawl {resolved_crawl_id[:SHORT_ID_LENGTH]}")
                for column in ("URL", "Status", "Title", "Meta desc.", "H1s", "Issues"):
                    table.add_column(column, overflow="fold")
                shown = 0
                async with contextlib.aclosing(db.iter_crawl_pages(resolved_crawl_id)) as pages:
                    async for page in pages:
                        if issues_only and not page.issues:
                            continue
                        if shown >= limit:
                            break
                        status_text = str(page.status_code) if page.status_code else "-"
                        status_style = (
                            "green" if page.alive and page.status_code and page.status_code < 400 else "red"
                        )
                        table.add_row(
                            page.url,
                            f"[{status_style}]{status_text}[/{status_style}]",
                            "✓" if page.title else "[red]missing[/red]",
                            "✓" if page.meta_description else "[red]missing[/red]",
                            str(page.h1_count),
                            ", ".join(issue.value for issue in page.issues) or "-",
                        )
                        shown += 1
            else:
                table = Table(title=f"Links for crawl {resolved_crawl_id[:SHORT_ID_LENGTH]}")
                for column in ("Source page", "Target URL", "Kind", "Status", "Broken"):
                    table.add_column(column, overflow="fold")
                shown = 0
                async with contextlib.aclosing(
                    db.iter_crawl_links(resolved_crawl_id, broken_only=broken_only)
                ) as links:
                    async for link in links:
                        if shown >= limit:
                            break
                        table.add_row(
                            link.source_page_url,
                            link.target_url,
                            link.link_kind.value,
                            str(link.status_code) if link.status_code else (link.error or "-"),
                            "[red]yes[/red]" if link.is_broken else "no",
                        )
                        shown += 1

            if shown == 0:
                console.print("[yellow]Nothing matches these filters.[/yellow]")
            else:
                console.print(table)
                console.print(f"[dim]Showing {shown} row(s) (--limit {limit}).[/dim]")
        finally:
            await db.close()

    asyncio.run(_do_show())


@app.command()
def stats(
    scan_id: Annotated[
        str | None, typer.Argument(help="Scan ID (full or a short prefix). Omit to list all scans.")
    ] = None,
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
) -> None:
    """Show aggregate statistics for one scan, or list all recorded scans.

    Examples:

        redirecthunter stats

        redirecthunter stats 3f9a1c2e-...

        redirecthunter stats 3f9a1c2e   # a short, unambiguous prefix also works
    """
    configure_logging(LogLevel.ERROR, quiet=True)  # stats is a read-only report; suppress INFO noise

    async def _show_stats() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            if scan_id is not None:
                try:
                    resolved_scan_id = await db.resolve_scan_id(scan_id)
                except DatabaseError as exc:
                    console.print(f"[bold red]{exc}[/bold red]")
                    raise typer.Exit(code=1) from None
                summary = await db.get_scan_summary(resolved_scan_id)
                if summary is None:
                    console.print(f"[bold red]No such scan:[/bold red] {scan_id}")
                    raise typer.Exit(code=1)
                _print_summary_table(summary)
            else:
                summaries = await db.list_scans()
                if not summaries:
                    console.print("[yellow]No scans recorded in this database yet.[/yellow]")
                    return
                table = Table(title=f"Scans in {database}")
                for column in ("Scan ID", "Label", "Status", "Progress", "Alive", "Dead", "Redirects", "Cloudflare"):
                    table.add_column(column)
                for s in summaries:
                    table.add_row(
                        s.scan_id[:SHORT_ID_LENGTH],
                        s.label or "-",
                        s.status.value,
                        f"{s.completed}/{s.total_urls} ({s.progress_pct}%)",
                        f"[green]{s.alive}[/green]",
                        f"[red]{s.dead}[/red]",
                        str(s.redirects_found),
                        str(s.cloudflare_protected),
                    )
                console.print(table)
                console.print(
                    "[dim]Tip: the shortened Scan ID above works directly with other commands, "
                    "e.g. `redirecthunter show " + summaries[0].scan_id[:SHORT_ID_LENGTH] + "`.[/dim]"
                )
        finally:
            await db.close()

    asyncio.run(_show_stats())


@app.command(name="export")
def export_cmd(
    scan_id: Annotated[str, typer.Argument(help="Scan ID (full or a short prefix).")],
    output: Annotated[Path, typer.Argument(help="Output file path.")],
    format: Annotated[  # noqa: A002
        ExportFormat, typer.Option("--format", case_sensitive=False, help="Export format.")
    ] = ExportFormat.CSV,
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
    alive_only: Annotated[
        bool, typer.Option("--alive-only", help="Export only results where the target responded.")
    ] = False,
    redirects_only: Annotated[
        bool, typer.Option("--redirects-only", help="Export only results where a redirect was detected.")
    ] = False,
    cloudflare_only: Annotated[
        bool, typer.Option("--cloudflare-only", help="Export only Cloudflare-protected results.")
    ] = False,
    has_link_only: Annotated[
        bool,
        typer.Option(
            "--has-link-only",
            help="Export only results whose terminal page body contains a navigable "
            "<a href> link (e.g. a manual 'click here to continue' interstitial). "
            "Only ever set on scans run with --method GET -- HEAD scans have no body "
            "to inspect, so this always exports 0 rows for them.",
        ),
    ] = False,
    status_code: Annotated[
        list[str] | None,
        typer.Option(
            "--status-code",
            help="Export only results matching a status code (e.g. '301') or class "
            "(e.g. '3xx'). Repeatable and/or comma-separated; any match passes, "
            "e.g. --status-code 301,302 --status-code 4xx.",
        ),
    ] = None,
) -> None:
    """Export a scan's results to CSV, JSON, or a standalone SQLite file.

    The --*-only filters mirror `show`'s filters and can be combined (a
    row must pass all of them to be exported). They are only supported
    for --format csv/json; --format sqlite always exports every result
    (export unfiltered, then query the file directly, or use CSV/JSON).

    Examples:

        redirecthunter export 3f9a1c2e-... results.csv

        redirecthunter export 3f9a1c2e-... redirects.csv --redirects-only

        redirecthunter export 3f9a1c2e-... links.csv --has-link-only

        redirecthunter export 3f9a1c2e-... redirects_301.csv --status-code 301

        redirecthunter export 3f9a1c2e-... redirects_broken.csv --status-code 4xx,5xx

        redirecthunter export 3f9a1c2e-... results.json --format json

        redirecthunter export 3f9a1c2e-... results.db --format sqlite
    """
    configure_logging(LogLevel.ERROR, quiet=True)
    status_codes, status_classes = _parse_status_filter(status_code)
    result_filter = ExportFilter(
        alive_only=alive_only,
        redirects_only=redirects_only,
        cloudflare_only=cloudflare_only,
        has_link_only=has_link_only,
        status_codes=status_codes,
        status_classes=status_classes,
    )

    async def _do_export() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_scan_id = await db.resolve_scan_id(scan_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            exporter = Exporter(db)
            with console.status(f"Exporting scan {resolved_scan_id} to {output} ({format.value})..."):
                try:
                    count = await exporter.export(resolved_scan_id, output, format, result_filter)
                except ExportError as exc:
                    console.print(f"[bold red]Export error:[/bold red] {exc}")
                    raise typer.Exit(code=1) from None
            console.print(f"[bold green]Exported {count} results[/bold green] to {output}")
        finally:
            await db.close()

    asyncio.run(_do_export())


@app.command(name="show")
def show_cmd(
    scan_id: Annotated[str, typer.Argument(help="Scan ID (full or a short prefix).")],
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
    limit: Annotated[int, typer.Option("--limit", help="Maximum number of results to display.")] = 50,
    alive_only: Annotated[bool, typer.Option("--alive-only", help="Show only responsive targets.")] = False,
    redirects_only: Annotated[
        bool, typer.Option("--redirects-only", help="Show only results where a redirect was detected.")
    ] = False,
    cloudflare_only: Annotated[
        bool, typer.Option("--cloudflare-only", help="Show only Cloudflare-protected results.")
    ] = False,
    has_link_only: Annotated[
        bool,
        typer.Option(
            "--has-link-only",
            help="Show only results whose terminal page body contains a navigable "
            "<a href> link (e.g. a manual 'click here to continue' interstitial). "
            "Only ever set on scans run with --method GET -- HEAD scans have no body "
            "to inspect, so this always shows 0 rows for them.",
        ),
    ] = False,
    status_code: Annotated[
        list[str] | None,
        typer.Option(
            "--status-code",
            help="Show only results matching a status code (e.g. '301') or class "
            "(e.g. '3xx'). Repeatable and/or comma-separated; any match passes, "
            "e.g. --status-code 301,302 --status-code 4xx.",
        ),
    ] = None,
) -> None:
    """Display individual results for a scan in a Rich table, with optional filters.

    Examples:

        redirecthunter show 3f9a1c2e-...

        redirecthunter show 3f9a1c2e-... --redirects-only --limit 100

        redirecthunter show 3f9a1c2e-... --has-link-only

        redirecthunter show 3f9a1c2e-... --status-code 404,410

        redirecthunter show 3f9a1c2e   # a short, unambiguous prefix also works
    """
    configure_logging(LogLevel.ERROR, quiet=True)
    status_codes, status_classes = _parse_status_filter(status_code)

    async def _do_show() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_scan_id = await db.resolve_scan_id(scan_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            table = Table(title=f"Results — {resolved_scan_id}")
            for column in (
                "Source URL",
                "Status",
                "Type",
                "Final URL",
                "Body Link",
                "Hops",
                "Server",
                "CF",
                "Alive",
                "Latency",
            ):
                table.add_column(column, overflow="fold")

            shown = 0
            async with contextlib.aclosing(db.iter_results(resolved_scan_id)) as results:
                async for result in results:
                    if alive_only and not result.alive:
                        continue
                    if redirects_only and result.redirect_type.value == "none":
                        continue
                    if cloudflare_only and not result.fingerprint.cloudflare.is_cloudflare:
                        continue
                    if has_link_only and not result.body_link:
                        continue
                    if status_codes or status_classes:
                        code = result.status_code
                        if code is None or (
                            code not in status_codes and (code // 100) not in status_classes
                        ):
                            continue
                    if shown >= limit:
                        break

                    table.add_row(
                        result.source_url,
                        str(result.status_code) if result.status_code is not None else "-",
                        result.redirect_type.value,
                        result.final_url or "-",
                        result.body_link or "-",
                        str(result.hop_count),
                        result.server or "-",
                        "yes" if result.fingerprint.cloudflare.is_cloudflare else "no",
                        "[green]yes[/green]" if result.alive else "[red]no[/red]",
                        f"{result.latency_ms:.0f} ms",
                    )
                    shown += 1

            console.print(table)
            console.print(f"[dim]Showing {shown} result(s) (limit={limit}).[/dim]")
        finally:
            await db.close()

    asyncio.run(_do_show())


class OutputField(str, Enum):
    """What to write per line when `find --output` is used."""

    DESTINATION = "destination"
    SOURCE = "source"
    BOTH = "both"


@app.command(name="find")
def find_cmd(
    scan_id: Annotated[str, typer.Argument(help="Scan ID (full or a short prefix).")],
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
    external_domain: Annotated[
        str | None,
        typer.Option(
            "--external-domain",
            "--domain",
            help="Domain to compare against. Default: auto-detected from the scan's --target.",
        ),
    ] = None,
    invert: Annotated[
        bool,
        typer.Option(
            "--invert",
            help="Show redirects that MATCH the domain instead of ones outside it "
            "(e.g. confirm which source URLs correctly redirect to your target).",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Save results as a plain list (one entry per line) to this file, instead of printing a table.",
        ),
    ] = None,
    field: Annotated[
        OutputField,
        typer.Option(
            "--field",
            case_sensitive=False,
            help="With --output: 'destination' (default), 'source', or 'both' "
            "('source_url -> destination' per line).",
        ),
    ] = OutputField.DESTINATION,
    limit: Annotated[int, typer.Option("--limit", help="Maximum rows shown in the terminal table.")] = 50,
) -> None:
    """Find redirects that land outside a given domain -- or, with --invert, that correctly land inside it.

    Without --output, prints a Rich table (like `show`). With --output,
    writes a plain list -- one entry per line, nothing else -- to the
    given file.

    Without --invert (default), this answers the open-redirect audit
    question: does a redirect endpoint send visitors somewhere outside the
    intended/authorized domain?

    With --invert, it answers the opposite, often equally useful,
    question: out of many candidate redirect/backlink endpoints, which
    ones actually, verifiably redirect to your domain? (Many public
    ad-click/redirect services don't reliably forward to the URL you give
    them -- some check the User-Agent or Referer and only redirect real
    browsers, others point to an unrelated default page. --invert filters
    down to the ones confirmed working.)

    Examples:

        redirecthunter find 3f9a1c2e

        redirecthunter find 3f9a1c2e --output external_redirects.txt

        redirecthunter find 3f9a1c2e --invert --field source --output confirmed_backlinks.txt
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    async def _do_find() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_scan_id = await db.resolve_scan_id(scan_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            domain = external_domain
            if domain is None:
                config = await db.get_scan_config(resolved_scan_id)
                if config is None or not config.target:
                    console.print(
                        "[bold red]Could not auto-detect a target domain for this scan[/bold red] "
                        "(no --target was recorded). Pass --external-domain explicitly."
                    )
                    raise typer.Exit(code=1)
                domain = urlparse(config.target).hostname or config.target

            matches: list[tuple[RedirectResult, str]] = []
            async with contextlib.aclosing(db.iter_results(resolved_scan_id)) as results:
                async for result in results:
                    if result.redirect_type == RedirectType.NONE:
                        continue

                    # When the chain was followed (hop_count > 0), final_url is the true
                    # landing page. When it wasn't (--no-follow-redirects, or max-redirects
                    # hit immediately), final_url is just the request's own URL -- the real
                    # intended destination is the raw Location header instead.
                    destination = result.final_url
                    if result.hop_count == 0 and result.location:
                        destination = resolve_relative_url(result.expanded_url, result.location)
                    if not destination:
                        continue

                    outside = is_external_domain(destination, domain)
                    if outside == invert:  # skip when they match; keep only when they differ
                        continue
                    matches.append((result, destination))

            relation = "matching" if invert else "outside"

            if output is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("w", encoding="utf-8") as fh:
                    for r, destination in matches:
                        if field is OutputField.SOURCE:
                            fh.write(f"{r.source_url}\n")
                        elif field is OutputField.BOTH:
                            fh.write(f"{r.source_url} -> {destination}\n")
                        else:
                            fh.write(f"{destination}\n")
                console.print(
                    f"[bold green]{len(matches)} redirect(s)[/bold green] {relation} '{domain}' "
                    f"saved to {output}"
                )
                return

            table = Table(title=f"Redirects {relation} '{domain}' — {resolved_scan_id}")
            for column in ("Source URL", "Type", "Destination", "Server", "CF"):
                table.add_column(column, overflow="fold")
            for r, destination in matches[:limit]:
                table.add_row(
                    r.source_url,
                    r.redirect_type.value,
                    destination,
                    r.server or "-",
                    "yes" if r.fingerprint.cloudflare.is_cloudflare else "no",
                )
            console.print(table)
            console.print(
                f"[dim]Showing {min(len(matches), limit)} of {len(matches)} result(s) "
                f"(limit={limit}). Use --output to save the full list instead.[/dim]"
            )
        finally:
            await db.close()

    asyncio.run(_do_find())


@app.command(name="delete")
def delete_cmd(
    scan_id: Annotated[str, typer.Argument(help="Scan ID (full or a short prefix) to permanently delete.")],
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
    vacuum: Annotated[
        bool,
        typer.Option(
            "--vacuum",
            help="Reclaim disk space immediately after deleting (rewrites the whole file; "
            "can be slow on large databases). Without this, deleted rows free up space for "
            "future scans but the file itself doesn't shrink until `redirecthunter vacuum` is run.",
        ),
    ] = False,
) -> None:
    """Permanently delete a scan and all its results, chain, and header rows.

    This cannot be undone. The scan row and every row that references it
    (via `ON DELETE CASCADE`) are removed in one step.

    Examples:

        redirecthunter delete 3f9a1c2e

        redirecthunter delete 3f9a1c2e --yes --vacuum
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    async def _do_delete() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_scan_id = await db.resolve_scan_id(scan_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            summary = await db.get_scan_summary(resolved_scan_id)
            if summary is not None:
                console.print(
                    f"About to permanently delete scan [bold]{resolved_scan_id}[/bold] "
                    f"({summary.completed} results, label={summary.label or '-'})."
                )

            if not yes and not typer.confirm("This cannot be undone. Continue?"):
                console.print("[yellow]Cancelled.[/yellow]")
                raise typer.Exit(code=0)

            deleted_count = await db.delete_scan(resolved_scan_id)
            console.print(
                f"[bold green]Deleted scan {resolved_scan_id}[/bold green] "
                f"({deleted_count} results removed)."
            )

            if vacuum:
                with console.status("Reclaiming disk space (VACUUM)..."):
                    await db.vacuum()
                console.print("[dim]Disk space reclaimed.[/dim]")
            else:
                console.print(
                    "[dim]Note: the database file won't shrink until you run "
                    "`redirecthunter vacuum` (or pass --vacuum next time).[/dim]"
                )
        finally:
            await db.close()

    asyncio.run(_do_delete())


@app.command(name="vacuum")
def vacuum_cmd(
    database: Annotated[Path, typer.Option("--database", "--db", help="SQLite results database path.")] = Path(
        "redirecthunter.db"
    ),
) -> None:
    """Reclaim disk space freed by previously deleted scans.

    SQLite never shrinks a database file automatically after a `DELETE` —
    the freed pages just become available for future writes. This rewrites
    the entire file compactly, which is the only way to actually reduce
    the file size on disk. Needs roughly as much free disk space as the
    database itself, and can take a while on large databases.

    Example:

        redirecthunter vacuum --database redirecthunter.db
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    async def _do_vacuum() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        size_before = database.stat().st_size
        db = Database(database)
        await db.connect()
        try:
            with console.status("Reclaiming disk space (VACUUM)... this may take a while."):
                await db.vacuum()
        finally:
            await db.close()

        size_after = database.stat().st_size
        reclaimed = size_before - size_after
        pct = (reclaimed / size_before * 100) if size_before else 0.0
        console.print(
            f"[bold green]Done.[/bold green] {size_before:,} -> {size_after:,} bytes "
            f"({reclaimed:,} bytes reclaimed, {pct:.1f}%)."
        )

    asyncio.run(_do_vacuum())


#: Maps a file extension to its corresponding RedactFormat, used to infer
#: `redact-target`'s output format from `-o`'s extension when `-f` is
#: omitted -- mirrors the old script's auto-detect-from-extension behavior.
_REDACT_EXTENSION_FORMAT_MAP: dict[str, RedactFormat] = {
    ".csv": RedactFormat.CSV,
    ".json": RedactFormat.JSON,
    ".db": RedactFormat.SQLITE,
    ".sqlite": RedactFormat.SQLITE,
    ".sqlite3": RedactFormat.SQLITE,
}


def _infer_redact_format(output: Path | None) -> RedactFormat:
    """Infer the redact-target output format from `-o`'s extension.

    Falls back to txt when there's no output path, or the extension isn't
    recognized -- matching the old script's `*) FORMAT=txt` default case.
    """
    if output is None:
        return RedactFormat.TXT
    return _REDACT_EXTENSION_FORMAT_MAP.get(output.suffix.lower(), RedactFormat.TXT)


@app.command(name="redact-target")
def redact_target_cmd(
    input_file: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="Plain-text file of URLs, one per line."),
    ],
    domain: Annotated[str, typer.Option("--domain", "-d", help="Domain to search for and replace.")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output file path. Default: stdout. Required when --format is sqlite.",
        ),
    ] = None,
    format: Annotated[  # noqa: A002 - CLI flag name intentionally shadows the builtin
        RedactFormat | None,
        typer.Option(
            "--format",
            "-f",
            case_sensitive=False,
            help="Output format: txt, csv, json, or sqlite. Inferred from -o's extension if omitted, else txt.",
        ),
    ] = None,
    token: Annotated[
        str, typer.Option("--token", "-t", help="Replacement token.")
    ] = TARGET_PLACEHOLDER,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose", "-v", help="Report each unmatched line and the total unmatched count to stderr."
        ),
    ] = False,
) -> None:
    """Replace occurrences of a domain with a token across a file of URLs.

    Reads a plain-text file of one URL per line, replaces occurrences of
    ``--domain`` with ``--token`` (``{TARGET}`` by default), and writes the
    result to stdout or a file in txt, csv, json, or sqlite format. Lines
    with no domain match are written through unchanged -- no data is
    silently dropped.

    Examples:

        redirecthunter redact-target urls.txt -d medilana.id

        redirecthunter redact-target urls.txt -d medilana.id -o out.csv

        redirecthunter redact-target urls.txt -d medilana.id -o out.json --format json

        redirecthunter redact-target urls.txt -d medilana.id -o out.db --format sqlite

        redirecthunter redact-target urls.txt -d medilana.id --verbose > out.txt
    """
    resolved_format = format if format is not None else _infer_redact_format(output)

    if resolved_format is RedactFormat.SQLITE and output is None:
        console.print(
            "[bold red]--format sqlite requires -o/--output[/bold red] "
            "(SQLite is a binary file format and can't be written to stdout)."
        )
        raise typer.Exit(code=1)

    rows = list(iter_redacted_rows(read_lines(input_file), domain, token=token))
    unmatched = [original for _target_url, original, matched in rows if not matched]

    if resolved_format is RedactFormat.SQLITE:
        assert output is not None  # --format sqlite requires -o/--output, checked above
        count = write_sqlite_rows(rows, output)
    else:
        writer = TEXT_WRITERS[resolved_format]
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("w", newline="", encoding="utf-8") as fh:
                count = writer(rows, fh)
        else:
            count = writer(rows, sys.stdout)

    if verbose:
        for line in unmatched:
            print(f"[SKIP] no domain match: {line}", file=sys.stderr)
        print(f"\nTotal lines without a match: {len(unmatched)}", file=sys.stderr)

    if output is not None:
        console.print(
            f"[bold green]{count} line(s) written[/bold green] to {output} ({resolved_format.value})"
        )


@app.command(name="expand-target")
def expand_target_cmd(
    input_file: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, help="Plain-text file of {TARGET}-templated URLs, one per line."
        ),
    ],
    target: Annotated[
        str, typer.Option("--target", help="Replacement value substituted for {TARGET}.")
    ],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output file path. Default: stdout.")
    ] = None,
    encode: Annotated[
        bool,
        typer.Option("--encode", help="Percent-encode --target's value before substitution."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Report each line with no {TARGET} token and the total such count to stderr.",
        ),
    ] = False,
) -> None:
    """Expand {TARGET} templates in a file into real URLs against a chosen target.

    The reverse of ``redact-target``: reads a plain-text file of
    ``{TARGET}``-templated URLs and writes real, ready-to-request URLs --
    without running a full ``scan``. Always plain-text in, plain-text out.
    Lines with no ``{TARGET}`` token are written through unchanged.

    Examples:

        redirecthunter expand-target templates.txt --target https://example.org

        redirecthunter expand-target templates.txt --target https://example.org --encode

        redirecthunter expand-target templates.txt --target https://example.org -o out.txt --verbose
    """
    lines = list(iter_expanded_lines(read_lines(input_file), target, url_encode=encode))
    untemplated = [expanded for expanded, had_placeholder in lines if not had_placeholder]

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as fh:
            count = write_expanded_lines(lines, fh)
    else:
        count = write_expanded_lines(lines, sys.stdout)

    if verbose:
        for line in untemplated:
            print(f"[SKIP] no {{TARGET}} token: {line}", file=sys.stderr)
        print(f"\nTotal lines without a {{TARGET}} token: {len(untemplated)}", file=sys.stderr)

    if output is not None:
        console.print(f"[bold green]{count} line(s) written[/bold green] to {output}")


#: Column order for `bl-export`. Mirrors `_CRAWL_PAGE_COLUMNS`'s convention:
#: nested structures (notes) are summarized as a delimited string rather
#: than exploded into sparse columns.


def _backlink_result_to_row(result: BacklinkResult) -> list[str | int | float]:
    """Flatten one BacklinkResult into a CSV/JSON row matching BACKLINK_RESULT_COLUMNS."""
    return [
        result.source_url,
        result.final_url or "",
        result.status_code if result.status_code is not None else "",
        "yes" if result.match_found else "no",
        result.match_type,
        result.matched_href or "",
        result.rel or "",
        result.target or "",
        result.matched_target or "",
        "yes" if result.blocked else "no",
        "yes" if result.requires_login else "no",
        result.text_mentions,
        result.robots_meta or "",
        result.robots_header or "",
        result.error or "",
        " | ".join(result.notes),
    ]


async def _write_backlink_export(
    rows: AsyncGenerator[BacklinkResult],
    output_path: Path,
    fmt: ExportFormat,
) -> int:
    """Stream a bl-export row source to CSV or JSON at ``output_path``.

    Deliberately its own small streaming writer, matching `crawl-export`'s
    `_write_crawl_export` -- a BacklinkResult has different columns from
    both a `RedirectResult` (`Exporter`'s shape) and a crawl page/link, and
    simply streams straight from `Database.iter_backlink_results` with no
    per-row transformation `ExportFilter` would otherwise provide.

    ``rows`` is wrapped in ``contextlib.aclosing(...)`` even though this
    loop runs to natural exhaustion (never ``break``s) -- ticket 04
    requires every call site of `iter_backlink_results()` to use
    `aclosing`, not just the ones that break early, so this stays correct
    if a future `--limit` is ever added here without anyone having to
    remember the rule.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    if fmt is ExportFormat.CSV:
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(BACKLINK_RESULT_COLUMNS)
            async with contextlib.aclosing(rows) as safe_rows:
                async for row in safe_rows:
                    writer.writerow(_backlink_result_to_row(row))
                    count += 1
    else:  # ExportFormat.JSON
        with output_path.open("wb") as fh:
            fh.write(b"[")
            first = True
            async with contextlib.aclosing(rows) as safe_rows:
                async for row in safe_rows:
                    record = dict(zip(BACKLINK_RESULT_COLUMNS, _backlink_result_to_row(row), strict=True))
                    if not first:
                        fh.write(b",")
                    fh.write(orjson.dumps(record))
                    first = False
                    count += 1
            fh.write(b"]")
    return count


def _print_backlink_summary_table(summary: BacklinkCheckSummary) -> None:
    """Render a BacklinkCheckSummary as a two-column Rich table. Mirrors ``_print_crawl_summary_table``."""
    table = Table(title=f"Backlink Check Summary: {summary.backlink_id[:SHORT_ID_LENGTH]}", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Label", summary.label or "-")
    table.add_row("Domain", summary.domain)
    table.add_row("Status", summary.status.value)
    table.add_row("Total URLs", str(summary.total_urls))
    table.add_row("Confirmed", f"[green]{summary.confirmed}[/green]")
    table.add_row("Indirect only", f"[yellow]{summary.indirect}[/yellow]" if summary.indirect else "0")
    table.add_row(
        "Text-only mention",
        f"[yellow]{summary.text_mention_only}[/yellow]" if summary.text_mention_only else "0",
    )
    table.add_row("Not found", str(summary.not_found))
    table.add_row("Blocked (anti-scraping)", f"[yellow]{summary.blocked}[/yellow]" if summary.blocked else "0")
    table.add_row("Requires login", f"[yellow]{summary.requires_login}[/yellow]" if summary.requires_login else "0")
    table.add_row("Errors", f"[red]{summary.error}[/red]" if summary.error else "0")
    table.add_row("Started", str(summary.started_at) if summary.started_at else "-")
    table.add_row("Finished", str(summary.finished_at) if summary.finished_at else "-")
    console.print(table)


def _backlink_progress_description(
    checked: int, confirmed: int, broken_or_blocked: int, workers: int, *, browser: bool
) -> str:
    """Build the dynamic status line for a running bl-check. Mirrors ``_crawl_progress_description``."""
    label = "workers" if not browser else "tabs"
    return (
        f"[bold cyan]RedirectHunter Backlink Check[/bold cyan] "
        f"{label}={workers} "
        f"checked={checked} "
        f"[green]confirmed={confirmed}[/green] "
        f"[yellow]blocked/error={broken_or_blocked}[/yellow]"
    )


def _build_per_url_targets(candidates: Sequence[CandidateURL]) -> dict[str, frozenset[str]]:
    """Build the per-URL target override map from each candidate's ``row_metadata["target"]``.

    Only rows that actually carry an override appear in the returned dict
    -- see ``_split_target_override``/``_split_target_list`` (TXT), the
    ``target`` CSV column, and the JSON ``"target"`` key, all of which
    populate this same reserved ``row_metadata`` key as a
    ``tuple[str, ...]`` of one or more targets (a plain ``str`` is also
    accepted here for callers that build ``row_metadata`` by hand rather
    than through the loader). Consumed by ``run_backlink_checks``/
    ``run_backlink_checks_browser``'s ``per_url_targets`` parameter, which
    falls back to the run's (or, in ``bl-chain``, the tier's) default
    target set for every URL not present here.

    A one-element override produces the same one-element ``frozenset`` it
    always has; a multi-element override (``target1;target2`` in the
    source file) produces a multi-member ``frozenset`` -- the row matches
    if the page links to *any* of them, and ``BacklinkResult.matched_target``
    records which one actually matched.
    """
    overrides: dict[str, frozenset[str]] = {}
    for candidate in candidates:
        raw_target = candidate.row_metadata.get("target")
        targets: tuple[str, ...]
        if isinstance(raw_target, str):
            targets = (raw_target,) if raw_target.strip() else ()
        elif isinstance(raw_target, (tuple, list)):
            targets = tuple(t for t in raw_target if isinstance(t, str) and t.strip())
        else:
            targets = ()
        if targets:
            overrides[candidate.raw_url] = frozenset(normalize_domain(t) for t in targets)
    return overrides


async def _run_backlink_check(
    config: BacklinkCheckConfig,
    urls: list[str],
    *,
    per_url_targets: Mapping[str, frozenset[str]] | None = None,
    per_url_account_id: Mapping[str, str] | None = None,
    account_headers: Mapping[str, Mapping[str, str]] | None = None,
) -> None:
    """Execution path for the ``bl-check`` command: run the checker, persisting results as they complete."""
    database = Database(config.database_path)
    await database.connect()

    summary: BacklinkCheckSummary | None = None
    try:
        await database.create_backlink_check(config, total_urls=len(urls))
        mode = "browser (Playwright)" if config.browser else "httpx"
        console.print(
            f"Starting backlink check [bold]{config.backlink_id}[/bold] "
            f"(domain={config.domain}, {len(urls)} URL(s), mode={mode}, concurrency={config.concurrency})"
        )

        counters = {"checked": 0, "confirmed": 0, "broken_or_blocked": 0}
        progress = _build_progress()
        with progress:
            task_id = progress.add_task(
                _backlink_progress_description(0, 0, 0, config.concurrency, browser=config.browser),
                total=len(urls),
            )

            async def on_result(result: BacklinkResult) -> None:
                await database.save_backlink_result(config.backlink_id, result)
                counters["checked"] += 1
                if result.match_found:
                    counters["confirmed"] += 1
                if result.blocked or result.error:
                    counters["broken_or_blocked"] += 1
                progress.update(
                    task_id,
                    advance=1,
                    description=_backlink_progress_description(
                        counters["checked"],
                        counters["confirmed"],
                        counters["broken_or_blocked"],
                        config.concurrency,
                        browser=config.browser,
                    ),
                )

            default_targets = frozenset({config.domain})
            try:
                if config.browser:
                    await run_backlink_checks_browser(
                        urls,
                        default_targets,
                        concurrency=config.concurrency,
                        nav_timeout=config.nav_timeout,
                        render_wait=config.render_wait,
                        allow_subdomains=config.allow_subdomains,
                        check_indirect=config.check_indirect,
                        user_agent=config.user_agent,
                        headed=config.headed,
                        block_resources=config.block_resources,
                        per_url_targets=per_url_targets,
                        per_url_account_id=per_url_account_id,
                        account_headers=account_headers,
                        on_result=on_result,
                    )
                else:
                    await run_backlink_checks(
                        urls,
                        default_targets,
                        concurrency=config.concurrency,
                        timeout=config.timeout,
                        allow_subdomains=config.allow_subdomains,
                        check_indirect=config.check_indirect,
                        user_agent=config.user_agent,
                        per_url_targets=per_url_targets,
                        per_url_account_id=per_url_account_id,
                        account_headers=account_headers,
                        on_result=on_result,
                    )
                await database.update_backlink_check_status(
                    config.backlink_id, RunStatus.COMPLETED, finished=True
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                await database.update_backlink_check_status(
                    config.backlink_id, RunStatus.INTERRUPTED, finished=True
                )
                console.print("\n[yellow]Backlink check interrupted.[/yellow]")
                raise
            except PlaywrightNotInstalledError as exc:
                await database.update_backlink_check_status(
                    config.backlink_id, RunStatus.FAILED, finished=True
                )
                console.print(f"\n[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
            except Exception:
                await database.update_backlink_check_status(
                    config.backlink_id, RunStatus.FAILED, finished=True
                )
                raise

        summary = await database.get_backlink_check_summary(config.backlink_id)
    finally:
        await database.close()

    if summary is not None:
        _print_backlink_summary_table(summary)
        console.print(f"\n[bold]backlink_id:[/bold] {config.backlink_id}")


@app.command(name="bl-check")
def bl_check(
    input_file: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="TXT/CSV/JSON/SQLite file of URLs to check."),
    ],
    domain: Annotated[
        str | None,
        typer.Option(
            "-d",
            "--domain",
            help="Target domain to look for, e.g. medilana.id. Can also be set as bl_check.domain "
            "in redirecthunter.yaml -- see --config.",
        ),
    ] = None,
    format: Annotated[  # noqa: A002
        InputFormat | None,
        typer.Option("--format", case_sensitive=False, help="Input file format. Inferred from extension if omitted."),
    ] = None,
    input_column: Annotated[
        str, typer.Option("--input-column", help="URL column name for CSV input. Default: 'url'.")
    ] = "url",
    concurrency: Annotated[
        int | None,
        typer.Option(
            "-c",
            "--concurrency",
            help="Concurrent workers. Default: 8 (httpx mode) or 4 (--browser mode, real page loads are heavier).",
        ),
    ] = None,
    timeout: Annotated[
        float | None, typer.Option("-t", "--timeout", help="Per-request timeout, seconds. httpx mode only. Default: 15.")
    ] = None,
    exact: Annotated[
        bool | None,
        typer.Option(
            "--exact/--no-exact",
            help="Match the domain exactly -- do not count subdomains (blog.medilana.id) as a match.",
        ),
    ] = None,
    strict: Annotated[
        bool | None,
        typer.Option("--strict/--no-strict", help="Skip weaker/indirect (tracker-embedded) match signals."),
    ] = None,
    user_agent: Annotated[
        str | None, typer.Option("-u", "--agent", help="User-Agent header sent with every request.")
    ] = None,
    accounts_file: Annotated[
        Path | None,
        typer.Option(
            "--accounts-file",
            exists=True,
            dir_okay=False,
            help=(
                "Registry of per-account session headers, 'account_id|Name: Value' per line "
                "(repeatable per account for multiple headers). Pair with an input file where "
                "individual rows are prefixed 'account_id|https://...' (TXT), have an "
                "'account_id' column (CSV), or an \"account_id\" key (JSON) -- for the case "
                "where one domain (e.g. facebook.com) needs many different sessions, one per "
                "row. See examples/bl-check-accounts.txt. Rows without an account_id are "
                "checked as normal, unauthenticated requests. Keep this file out of version "
                "control -- it holds real session cookies. Can also be set as bl_check.accounts_file "
                "in redirecthunter.yaml so it doesn't need retyping every run -- see --config."
            ),
        ),
    ] = None,
    browser: Annotated[
        bool | None,
        typer.Option(
            "--browser/--no-browser",
            help=(
                "Render with a real (Playwright) browser instead of a plain HTTP GET -- for pages "
                "whose links are added by client-side JS after load. Needs the `redirecthunter\\[js]` "
                "extra: pip install redirecthunter\\[js] && playwright install chromium."
            ),
        ),
    ] = None,
    headed: Annotated[
        bool | None,
        typer.Option(
            "--headed/--no-headed", help="Show the real browser window instead of running headless. Only with --browser."
        ),
    ] = None,
    nav_timeout: Annotated[
        float | None, typer.Option("--nav-timeout", help="Seconds to wait for navigation. Only with --browser. Default: 30.")
    ] = None,
    render_wait: Annotated[
        float | None,
        typer.Option(
            "--render-wait",
            help="Seconds to wait for the page to go network-idle after load. Only with --browser. Default: 8.",
        ),
    ] = None,
    label: Annotated[
        str | None, typer.Option("-l", "--label", help="Human-readable label for this run.")
    ] = None,
    database: Annotated[
        Path | None, typer.Option("--database", "--db", help="SQLite results database path. Default: redirecthunter.db.")
    ] = None,
    config_file: Annotated[
        Path | None,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            help=(
                "YAML config file. Auto-discovered (redirecthunter.yaml et al.) if omitted. "
                "Presets read from its 'bl_check:' section -- domain, accounts_file, "
                "concurrency, timeout, exact, strict, user_agent, browser, headed, nav_timeout, "
                "render_wait, label, database -- so they don't need retyping every run. "
                "Priority: CLI flag > config file > built-in default. See examples/redirecthunter.yaml."
            ),
        ),
    ] = None,
    log_level: Annotated[LogLevel, typer.Option("--log-level", case_sensitive=False, help="Logging verbosity.")] = LogLevel.INFO,
    log_file: Annotated[Path | None, typer.Option("--log-file", help="Also write logs to this file.")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress console log output.")] = False,
) -> None:
    """Check every URL in a file for a genuine outbound link to a target domain.

    Answers a different question from `scan` -- does a page's rendered
    HTML genuinely *link to* the target domain, not whether a URL
    *redirects to* it -- persisting the run and every per-URL result to
    `redirecthunter.db` so it can be revisited with `bl-stats`/
    `bl-show`/`bl-export`, the way `crawl`/`crawl-stats`/`crawl-show`/
    `crawl-export` already work for crawl mode. Every result also
    records the matched link's `target`/`rel` attributes and the page's
    robots signal (`<meta name="robots">` *and* the `X-Robots-Tag`
    header -- these can disagree, so both are kept), all visible via
    `bl-export`.

    By default this fetches with a plain HTTP GET (`httpx`), which never
    executes JavaScript -- pages whose links are injected by client-side
    JS after load won't show them. Pass `--browser` to render with a
    real (headless) Chromium via Playwright instead; needs the
    `redirecthunter\[js]` extra installed separately (`pip install
    redirecthunter\[js] && playwright install chromium`) since it's a
    ~300MB browser download most users of this command don't need.
    `--browser` mode is slower and much heavier per URL (a real page
    load, not a lightweight request), which is why its concurrency
    default is lower -- see `-c/--concurrency` above.

    This command family uses short flags (`-d`, `-c`, `-t`, `-u`, `-l`)
    as a deliberate exception to the rest of this CLI's long-flag-only
    convention -- see `AGENT.md`/`MEMORY.md`.

    Examples:

        redirecthunter bl-check examples/backlink.txt -d medilana.id

        redirecthunter bl-check backlinks.csv -d medilana.id -c 16 -t 20 --exact

        redirecthunter bl-check backlinks.txt -d medilana.id --strict -l "Q3 audit"

        redirecthunter bl-check backlinks.txt -d medilana.id --browser

        redirecthunter bl-check backlinks.txt -d medilana.id --accounts-file examples/bl-check-accounts.txt

        redirecthunter bl-check backlinks.txt --config redirecthunter.yaml

    On login-walled pages (see `requires_login` in results / `looks_like_login_wall`),
    `--accounts-file` lets you attach a session cookie from an already-authenticated
    browser session so the real page (not the login page) gets checked -- one
    session per `account_id`, referenced from the matching input row (see
    `examples/bl-check-accounts.txt`). This is strictly "bring your own
    session" -- there is no username/password/login-form automation anywhere
    in this command, in either httpx or `--browser` mode.

    `--config` reads `domain`, `accounts_file`, `concurrency`, `timeout`,
    `exact`, `strict`, `user_agent`, `browser`, `headed`, `nav_timeout`,
    `render_wait`, `label`, and `database` from `redirecthunter.yaml`'s
    `bl_check:` section (auto-discovered if omitted), so a standing set of
    flags doesn't need retyping every run. CLI flags always win over the
    config file. See `examples/redirecthunter.yaml`.
    """
    configure_logging(log_level, log_file, quiet=quiet)

    async def _prepare_and_run() -> None:
        try:
            resolved_config, resolved_accounts_file = build_backlink_check_config(
                input_path=input_file,
                input_format=format,
                domain=domain,
                concurrency=concurrency,
                timeout=timeout,
                exact=exact,
                strict=strict,
                user_agent=user_agent,
                accounts_file=accounts_file,
                browser=browser,
                headed=headed,
                nav_timeout=nav_timeout,
                render_wait=render_wait,
                label=label,
                database_path=database,
                config_file=config_file,
            )
        except ConfigError as exc:
            console.print(f"[bold red]Configuration error:[/bold red] {exc}")
            raise typer.Exit(code=1) from None

        loader_config = ScanConfig(
            input_path=input_file, input_format=resolved_config.input_format, input_column=input_column
        )
        try:
            candidates = list(load_candidates(loader_config))
        except LoaderError as exc:
            console.print(f"[bold red]Input error:[/bold red] {exc}")
            raise typer.Exit(code=1) from None

        urls = [candidate.raw_url for candidate in candidates]
        per_url_targets = _build_per_url_targets(candidates)
        per_url_account_id = _build_per_url_account_ids(candidates)

        if not urls:
            console.print("[yellow]No URLs found in input file. Nothing to do.[/yellow]")
            raise typer.Exit(code=0)

        if resolved_config.headed and not resolved_config.browser:
            console.print("[bold red]Configuration error:[/bold red] --headed only makes sense with --browser.")
            raise typer.Exit(code=1)

        account_lines = _read_lines_from_file(resolved_accounts_file) if resolved_accounts_file else []
        account_headers, account_warnings = _parse_account_headers(account_lines)
        for warning in account_warnings:
            console.print(f"[yellow]{warning}[/yellow]")

        missing_accounts = _validate_account_references(per_url_account_id, account_headers)
        if missing_accounts:
            console.print(
                f"[bold red]Configuration error:[/bold red] {len(missing_accounts)} account_id(s) "
                f"referenced in {input_file} are not in the accounts registry"
                + (f" ({resolved_accounts_file})" if resolved_accounts_file else " (no --accounts-file given)")
                + f": {', '.join(missing_accounts)}"
            )
            raise typer.Exit(code=1)

        await _run_backlink_check(
            resolved_config,
            urls,
            per_url_targets=per_url_targets,
            per_url_account_id=per_url_account_id,
            account_headers=account_headers,
        )

    asyncio.run(_prepare_and_run())


async def _run_backlink_chain_tier(
    database: Database,
    config: BacklinkCheckConfig,
    urls: list[str],
    default_targets: frozenset[str],
    *,
    per_url_targets: Mapping[str, frozenset[str]] | None,
    per_url_account_id: Mapping[str, str] | None = None,
    account_headers: Mapping[str, Mapping[str, str]] | None = None,
    tier_number: int,
    tier_count: int,
) -> list[BacklinkResult]:
    """Run one tier of a ``bl-chain`` as an ordinary backlink-check run.

    Mirrors ``_run_backlink_check``'s execution shape (own progress bar,
    persistence via ``save_backlink_result``, lifecycle status updates)
    but is parameterized by an explicit ``default_targets`` -- only tier
    1 uses ``frozenset({domain})``, every later tier passes the target
    set derived from the previous tier's input URLs -- and returns the
    in-memory results so the caller can derive the *next* tier's default
    target set without a second database round-trip.
    """
    results: list[BacklinkResult] = []
    await database.create_backlink_check(config, total_urls=len(urls))
    mode = "browser (Playwright)" if config.browser else "httpx"
    targets_preview = ", ".join(sorted(default_targets)) if default_targets else "(none)"
    console.print(
        f"\n[bold]Tier {tier_number}/{tier_count}[/bold] -- backlink check "
        f"[bold]{config.backlink_id}[/bold] (targets={targets_preview}, input={config.input_path}, "
        f"{len(urls)} URL(s), mode={mode}, concurrency={config.concurrency})"
    )
    if not default_targets:
        console.print(
            "[yellow]Warning:[/yellow] this tier's derived default target set is empty "
            "(the previous tier had no confirmed matches under --require-confirmed-parent) -- "
            "every URL without its own per-row |target override will report not_found."
        )

    counters = {"checked": 0, "confirmed": 0, "broken_or_blocked": 0}
    progress = _build_progress()
    with progress:
        task_id = progress.add_task(
            _backlink_progress_description(0, 0, 0, config.concurrency, browser=config.browser),
            total=len(urls),
        )

        async def on_result(result: BacklinkResult) -> None:
            await database.save_backlink_result(config.backlink_id, result)
            results.append(result)
            counters["checked"] += 1
            if result.match_found:
                counters["confirmed"] += 1
            if result.blocked or result.error:
                counters["broken_or_blocked"] += 1
            progress.update(
                task_id,
                advance=1,
                description=_backlink_progress_description(
                    counters["checked"],
                    counters["confirmed"],
                    counters["broken_or_blocked"],
                    config.concurrency,
                    browser=config.browser,
                ),
            )

        try:
            if config.browser:
                await run_backlink_checks_browser(
                    urls,
                    default_targets,
                    concurrency=config.concurrency,
                    nav_timeout=config.nav_timeout,
                    render_wait=config.render_wait,
                    allow_subdomains=config.allow_subdomains,
                    check_indirect=config.check_indirect,
                    user_agent=config.user_agent,
                    headed=config.headed,
                    block_resources=config.block_resources,
                    per_url_targets=per_url_targets,
                    per_url_account_id=per_url_account_id,
                    account_headers=account_headers,
                    on_result=on_result,
                )
            else:
                await run_backlink_checks(
                    urls,
                    default_targets,
                    concurrency=config.concurrency,
                    timeout=config.timeout,
                    allow_subdomains=config.allow_subdomains,
                    check_indirect=config.check_indirect,
                    user_agent=config.user_agent,
                    per_url_targets=per_url_targets,
                    per_url_account_id=per_url_account_id,
                    account_headers=account_headers,
                    on_result=on_result,
                )
            await database.update_backlink_check_status(config.backlink_id, RunStatus.COMPLETED, finished=True)
        except (KeyboardInterrupt, asyncio.CancelledError):
            await database.update_backlink_check_status(config.backlink_id, RunStatus.INTERRUPTED, finished=True)
            raise
        except PlaywrightNotInstalledError:
            await database.update_backlink_check_status(config.backlink_id, RunStatus.FAILED, finished=True)
            raise
        except Exception:
            await database.update_backlink_check_status(config.backlink_id, RunStatus.FAILED, finished=True)
            raise

    return results


def _print_backlink_chain_summary_table(summary: BacklinkChainSummary) -> None:
    """Render a combined per-tier summary table for ``bl-chain``/``bl-chain-stats``."""
    table = Table(title=f"Backlink Chain Summary: {summary.chain_id[:SHORT_ID_LENGTH]}")
    for column in ("Tier", "Backlink ID", "Domain/Targets", "Confirmed", "Not found", "Blocked"):
        table.add_column(column)
    for index, tier in enumerate(summary.tiers, start=1):
        table.add_row(
            str(index),
            tier.backlink_id[:SHORT_ID_LENGTH],
            tier.domain,
            f"[green]{tier.confirmed}[/green]/{tier.total_urls}",
            str(tier.not_found),
            f"[yellow]{tier.blocked}[/yellow]" if tier.blocked else "0",
        )
    console.print(table)


@app.command(name="bl-chain")
def bl_chain(
    tier_paths: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Ordered tier input files (TXT/CSV/JSON), tier 1 first. At least 2 required.",
        ),
    ],
    domain: Annotated[
        str | None,
        typer.Option(
            "-d",
            "--domain",
            help="Root (tier 1) target domain to look for, e.g. medilana.id. Can also be set as "
            "bl_chain.domain in redirecthunter.yaml -- see --config.",
        ),
    ] = None,
    require_confirmed_parent: Annotated[
        bool | None,
        typer.Option(
            "--require-confirmed-parent/--no-require-confirmed-parent",
            help=(
                "Derive each tier's default target set only from the previous tier's confirmed "
                "(match_found) rows, instead of all of its input URLs. Off by default -- see "
                "the bl-chain docstring for why."
            ),
        ),
    ] = None,
    concurrency: Annotated[
        int | None,
        typer.Option(
            "-c",
            "--concurrency",
            help="Concurrent workers, applied per tier. Default: 8 (httpx mode) or 4 (--browser mode).",
        ),
    ] = None,
    timeout: Annotated[
        float | None, typer.Option("-t", "--timeout", help="Per-request timeout, seconds. httpx mode only.")
    ] = None,
    exact: Annotated[
        bool | None,
        typer.Option(
            "--exact/--no-exact", help="Match target domains exactly -- do not count subdomains as a match."
        ),
    ] = None,
    strict: Annotated[
        bool | None,
        typer.Option("--strict/--no-strict", help="Skip weaker/indirect (tracker-embedded) match signals."),
    ] = None,
    user_agent: Annotated[
        str | None, typer.Option("-u", "--agent", help="User-Agent header sent with every request.")
    ] = None,
    accounts_file: Annotated[
        Path | None,
        typer.Option(
            "--accounts-file",
            exists=True,
            dir_okay=False,
            help=(
                "Registry of per-account session headers. Same 'account_id|Name: Value' syntax "
                "and paired 'account_id|URL' row prefix as bl-check's --accounts-file, applied "
                "the same way across every tier. Can also be set as bl_chain.accounts_file in "
                "redirecthunter.yaml -- see --config."
            ),
        ),
    ] = None,
    browser: Annotated[
        bool | None,
        typer.Option(
            "--browser/--no-browser",
            help="Render every tier with a real (Playwright) browser instead of a plain HTTP GET.",
        ),
    ] = None,
    headed: Annotated[
        bool | None,
        typer.Option(
            "--headed/--no-headed", help="Show the real browser window instead of running headless. Only with --browser."
        ),
    ] = None,
    nav_timeout: Annotated[
        float | None, typer.Option("--nav-timeout", help="Seconds to wait for navigation. Only with --browser.")
    ] = None,
    render_wait: Annotated[
        float | None,
        typer.Option(
            "--render-wait",
            help="Seconds to wait for the page to go network-idle after load. Only with --browser.",
        ),
    ] = None,
    label: Annotated[
        str | None, typer.Option("-l", "--label", help="Human-readable label for this chain.")
    ] = None,
    database: Annotated[
        Path | None, typer.Option("--database", "--db", help="SQLite results database path.")
    ] = None,
    config_file: Annotated[
        Path | None,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            help=(
                "YAML config file. Auto-discovered (redirecthunter.yaml et al.) if omitted. "
                "Presets read from its 'bl_chain:' section -- domain, accounts_file, "
                "concurrency, timeout, exact, strict, user_agent, browser, headed, nav_timeout, "
                "render_wait, label, database -- so they don't need retyping every run. "
                "Priority: CLI flag > config file > built-in default. See examples/redirecthunter.yaml."
            ),
        ),
    ] = None,
    log_level: Annotated[LogLevel, typer.Option("--log-level", case_sensitive=False, help="Logging verbosity.")] = LogLevel.INFO,
    log_file: Annotated[Path | None, typer.Option("--log-file", help="Also write logs to this file.")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress console log output.")] = False,
) -> None:
    """Check a tiered/pyramid backlink structure: tier 1 vs -d/--domain, tier N>1 vs tier N-1's own hosts.

    Tier 1 is checked against ``-d/--domain`` exactly like `bl-check`.
    Tier N (N > 1) is, by default, checked against the set of hostnames
    extracted from tier N-1's own *input* URLs (every row, not just the
    ones tier N-1 itself confirmed -- see ``--require-confirmed-parent``
    to opt into the stricter, confirmed-only derivation instead). A
    row's own per-row ``|target`` override (see `bl-check`'s docstring)
    still wins over either derived default, within any tier.

    Each tier is persisted as an ordinary `bl-check` run (its own
    `backlink_id`, its own rows in `backlink_results`) -- `bl-stats`/
    `bl-show`/`bl-export` work on any individual tier exactly like they
    do for a plain `bl-check` run. Tier order is always the order tier
    files are given on the command line -- never inferred from filenames.

    This command family uses short flags (`-d`, `-c`, `-t`, `-u`, `-l`)
    as the same documented exception `bl-check` already carries -- see
    `AGENT.md`/`MEMORY.md`.

    Examples:

        redirecthunter bl-chain tier1.txt tier2.txt -d medilana.id

        redirecthunter bl-chain tier1.txt tier2.txt tier3.txt -d medilana.id --require-confirmed-parent

        redirecthunter bl-chain tier1.txt tier2.txt -d medilana.id -c 16 --exact -l "Q3 pyramid audit"

        redirecthunter bl-chain tier1.txt tier2.txt --accounts-file examples/bl-check-accounts.txt

        redirecthunter bl-chain tier1.txt tier2.txt --config redirecthunter.yaml

    `--config` reads `domain`, `accounts_file`, `concurrency`, `timeout`,
    `exact`, `strict`, `user_agent`, `browser`, `headed`, `nav_timeout`,
    `render_wait`, `label`, and `database` from `redirecthunter.yaml`'s
    `bl_chain:` section (auto-discovered if omitted), the same way
    `bl-check`'s `bl_check:` section works -- CLI flags always win. See
    `examples/redirecthunter.yaml`.
    """
    configure_logging(log_level, log_file, quiet=quiet)

    if len(tier_paths) < 2:
        console.print(
            "[bold red]Configuration error:[/bold red] bl-chain needs at least 2 tier files "
            "(a 1-tier chain is just bl-check)."
        )
        raise typer.Exit(code=1)

    async def _prepare_and_run() -> None:
        try:
            chain_config, resolved_accounts_file = build_backlink_chain_config(
                tier_paths=tier_paths,
                domain=domain,
                require_confirmed_parent=require_confirmed_parent,
                concurrency=concurrency,
                timeout=timeout,
                exact=exact,
                strict=strict,
                user_agent=user_agent,
                accounts_file=accounts_file,
                browser=browser,
                headed=headed,
                nav_timeout=nav_timeout,
                render_wait=render_wait,
                label=label,
                database_path=database,
                config_file=config_file,
            )
        except ConfigError as exc:
            console.print(f"[bold red]Configuration error:[/bold red] {exc}")
            raise typer.Exit(code=1) from None

        if chain_config.headed and not chain_config.browser:
            console.print("[bold red]Configuration error:[/bold red] --headed only makes sense with --browser.")
            raise typer.Exit(code=1)

        account_lines = _read_lines_from_file(resolved_accounts_file) if resolved_accounts_file else []
        account_headers, account_warnings = _parse_account_headers(account_lines)
        for warning in account_warnings:
            console.print(f"[yellow]{warning}[/yellow]")

        # Load every tier's candidates up front -- catches a bad input
        # file (missing URL column, unreadable format, etc.) before any
        # request goes out for tier 1, rather than mid-chain.
        tier_candidates: list[list[CandidateURL]] = []
        for path in tier_paths:
            resolved_format = infer_input_format(path)
            loader_config = ScanConfig(input_path=path, input_format=resolved_format, input_column="url")
            try:
                candidates = list(load_candidates(loader_config))
            except LoaderError as exc:
                console.print(f"[bold red]Input error ({path}):[/bold red] {exc}")
                raise typer.Exit(code=1) from None
            if not candidates:
                console.print(f"[bold red]Input error:[/bold red] {path} has no URLs. Aborting chain.")
                raise typer.Exit(code=1)
            tier_candidates.append(candidates)

        # Same fail-fast principle as the loader errors just above: validate
        # every tier's account_id references against the registry before
        # tier 1 ever runs, not mid-chain -- one accounts-file is shared
        # across all tiers (see --accounts-file's help text).
        tier_per_url_account_id: list[dict[str, str]] = [
            _build_per_url_account_ids(candidates) for candidates in tier_candidates
        ]
        all_missing_accounts = sorted(
            {
                account_id
                for per_url in tier_per_url_account_id
                for account_id in _validate_account_references(per_url, account_headers)
            }
        )
        if all_missing_accounts:
            console.print(
                f"[bold red]Configuration error:[/bold red] {len(all_missing_accounts)} account_id(s) "
                f"referenced across the chain's tiers are not in the accounts registry"
                + (f" ({resolved_accounts_file})" if resolved_accounts_file else " (no --accounts-file given)")
                + f": {', '.join(all_missing_accounts)}"
            )
            raise typer.Exit(code=1)

        db = Database(chain_config.database_path)
        await db.connect()
        try:
            await db.create_backlink_chain(chain_config)
            console.print(
                f"Starting backlink chain [bold]{chain_config.chain_id}[/bold] "
                f"({len(tier_paths)} tier(s), root domain={chain_config.domain})"
            )

            tier_results: list[list[BacklinkResult]] = []
            try:
                for tier_index, (path, candidates) in enumerate(zip(tier_paths, tier_candidates, strict=True)):
                    if tier_index == 0:
                        default_targets = frozenset({chain_config.domain})
                    else:
                        prev_candidates = tier_candidates[tier_index - 1]
                        prev_results = tier_results[tier_index - 1]
                        if chain_config.require_confirmed_parent:
                            source_urls = [r.source_url for r in prev_results if r.match_found]
                        else:
                            source_urls = [c.raw_url for c in prev_candidates]
                        default_targets = frozenset(normalize_domain(u) for u in source_urls)

                    urls = [candidate.raw_url for candidate in candidates]
                    per_url_targets = _build_per_url_targets(candidates)
                    per_url_account_id = tier_per_url_account_id[tier_index]
                    tier_domain_label = (
                        chain_config.domain
                        if tier_index == 0
                        else (", ".join(sorted(default_targets)) if default_targets else "(no targets)")
                    )

                    tier_config = BacklinkCheckConfig(
                        domain=tier_domain_label,
                        input_path=path,
                        input_format=infer_input_format(path),
                        allow_subdomains=chain_config.allow_subdomains,
                        check_indirect=chain_config.check_indirect,
                        concurrency=chain_config.concurrency,
                        timeout=chain_config.timeout,
                        user_agent=chain_config.user_agent,
                        database_path=chain_config.database_path,
                        label=(
                            f"{chain_config.label} - tier {tier_index + 1}"
                            if chain_config.label
                            else f"bl-chain tier {tier_index + 1}"
                        ),
                        browser=chain_config.browser,
                        headed=chain_config.headed,
                        nav_timeout=chain_config.nav_timeout,
                        render_wait=chain_config.render_wait,
                        block_resources=chain_config.block_resources,
                    )

                    results = await _run_backlink_chain_tier(
                        db,
                        tier_config,
                        urls,
                        default_targets,
                        per_url_targets=per_url_targets,
                        per_url_account_id=per_url_account_id,
                        account_headers=account_headers,
                        tier_number=tier_index + 1,
                        tier_count=len(tier_paths),
                    )
                    tier_results.append(results)
                    await db.link_chain_tier(chain_config.chain_id, tier_index, tier_config.backlink_id, path)

                await db.update_backlink_chain_status(chain_config.chain_id, RunStatus.COMPLETED, finished=True)
            except (KeyboardInterrupt, asyncio.CancelledError):
                await db.update_backlink_chain_status(chain_config.chain_id, RunStatus.INTERRUPTED, finished=True)
                console.print("\n[yellow]Backlink chain interrupted.[/yellow]")
                raise
            except PlaywrightNotInstalledError as exc:
                await db.update_backlink_chain_status(chain_config.chain_id, RunStatus.FAILED, finished=True)
                console.print(f"\n[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
            except Exception:
                await db.update_backlink_chain_status(chain_config.chain_id, RunStatus.FAILED, finished=True)
                raise

            summary = await db.get_backlink_chain_summary(chain_config.chain_id)
        finally:
            await db.close()

        if summary is not None:
            console.print()
            _print_backlink_chain_summary_table(summary)
        console.print(f"\n[bold]chain_id:[/bold] {chain_config.chain_id}")

    asyncio.run(_prepare_and_run())


@app.command(name="bl-stats")
def bl_stats(
    backlink_id: Annotated[
        str | None, typer.Argument(help="Backlink check ID (full or a short prefix). Omit to list all runs.")
    ] = None,
    database: Annotated[
        Path, typer.Option("--database", "--db", help="SQLite results database path.")
    ] = Path("redirecthunter.db"),
) -> None:
    """Show aggregate statistics for one backlink-check run, or list all recorded runs. Mirrors `crawl-stats`.

    Examples:

        redirecthunter bl-stats

        redirecthunter bl-stats 3f9a1c2e
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    async def _show_backlink_stats() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            if backlink_id is not None:
                try:
                    resolved_backlink_id = await db.resolve_backlink_check_id(backlink_id)
                except DatabaseError as exc:
                    console.print(f"[bold red]{exc}[/bold red]")
                    raise typer.Exit(code=1) from None
                summary = await db.get_backlink_check_summary(resolved_backlink_id)
                if summary is None:
                    console.print(f"[bold red]No such backlink check:[/bold red] {backlink_id}")
                    raise typer.Exit(code=1)
                _print_backlink_summary_table(summary)
            else:
                summaries = await db.list_backlink_checks()
                if not summaries:
                    console.print("[yellow]No backlink checks recorded in this database yet.[/yellow]")
                    return
                table = Table(title=f"Backlink checks in {database}")
                for column in ("Backlink ID", "Domain", "Label", "Status", "Confirmed", "Not found", "Blocked"):
                    table.add_column(column)
                for s in summaries:
                    table.add_row(
                        s.backlink_id[:SHORT_ID_LENGTH],
                        s.domain,
                        s.label or "-",
                        s.status.value,
                        f"[green]{s.confirmed}[/green]/{s.total_urls}",
                        str(s.not_found),
                        f"[yellow]{s.blocked}[/yellow]" if s.blocked else "0",
                    )
                console.print(table)
                console.print(
                    "[dim]Tip: the shortened Backlink ID above works directly with other commands, "
                    "e.g. `redirecthunter bl-show " + summaries[0].backlink_id[:SHORT_ID_LENGTH] + "`.[/dim]"
                )
        finally:
            await db.close()

    asyncio.run(_show_backlink_stats())


@app.command(name="bl-show")
def bl_show(
    backlink_id: Annotated[str, typer.Argument(help="Backlink check ID (full or a short prefix).")],
    confirmed: Annotated[
        bool, typer.Option("--confirmed", help="Only show URLs with a confirmed backlink match.")
    ] = False,
    type: Annotated[  # noqa: A002 - CLI flag name intentionally shadows the builtin
        str | None,
        typer.Option(
            "--type",
            help="Only show results with this match_type (e.g. text_mention_only, indirect_query, "
            "anchor, subdomain_anchor, final_url_is_target, not_found).",
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum rows to display.")] = 50,
    database: Annotated[
        Path, typer.Option("--database", "--db", help="SQLite results database path.")
    ] = Path("redirecthunter.db"),
) -> None:
    """Display individual per-URL backlink-check results in a Rich table. Mirrors `crawl-show --type links`.

    Examples:

        redirecthunter bl-show 3f9a1c2e

        redirecthunter bl-show 3f9a1c2e --confirmed

        redirecthunter bl-show 3f9a1c2e --type text_mention_only --limit 20
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    async def _do_show() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_backlink_id = await db.resolve_backlink_check_id(backlink_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            table = Table(title=f"Results for backlink check {resolved_backlink_id[:SHORT_ID_LENGTH]}")
            for column in ("Source URL", "Status", "Match", "Type", "Matched href", "Matched target", "Rel"):
                table.add_column(column, overflow="fold")
            shown = 0
            async with contextlib.aclosing(
                db.iter_backlink_results(resolved_backlink_id, confirmed_only=confirmed, match_type=type)
            ) as results:
                async for result in results:
                    if shown >= limit:
                        break
                    if result.error:
                        status_text = "[red]error[/red]"
                    elif result.blocked:
                        status_text = "[yellow]blocked[/yellow]"
                    elif result.requires_login:
                        status_text = "[yellow]login wall[/yellow]"
                    else:
                        status_text = str(result.status_code) if result.status_code else "-"
                    table.add_row(
                        result.source_url,
                        status_text,
                        "[green]yes[/green]" if result.match_found else "no",
                        result.match_type,
                        result.matched_href or "-",
                        result.matched_target or "-",
                        result.rel or "-",
                    )
                    shown += 1

            if shown == 0:
                console.print("[yellow]Nothing matches these filters.[/yellow]")
            else:
                console.print(table)
                console.print(f"[dim]Showing {shown} row(s) (--limit {limit}).[/dim]")
        finally:
            await db.close()

    asyncio.run(_do_show())


@app.command(name="bl-export")
def bl_export(
    backlink_id: Annotated[str, typer.Argument(help="Backlink check ID (full or a short prefix).")],
    output: Annotated[Path, typer.Option("-o", "--output", help="Output file path.")],
    format: Annotated[  # noqa: A002
        ExportFormat,
        typer.Option("-f", "--format", case_sensitive=False, help="Export format (csv or json; sqlite is not supported)."),
    ] = ExportFormat.CSV,
    confirmed: Annotated[
        bool, typer.Option("--confirmed", help="Only export URLs with a confirmed backlink match.")
    ] = False,
    database: Annotated[
        Path, typer.Option("--database", "--db", help="SQLite results database path.")
    ] = Path("redirecthunter.db"),
) -> None:
    """Export a backlink-check run's per-URL results to CSV or JSON. Mirrors `crawl-export`.

    Deliberately its own small streaming writer (not `Exporter`, which is
    ``RedirectResult``-shaped): a `BacklinkResult` has different columns
    entirely, and simply streams straight from
    `Database.iter_backlink_results` with no per-row transformation
    `ExportFilter` would otherwise provide. No `--format sqlite` in v1,
    matching `crawl-export`'s own documented v1 scope decision.

    Examples:

        redirecthunter bl-export 3f9a1c2e -o report.csv

        redirecthunter bl-export 3f9a1c2e -o confirmed.csv --confirmed

        redirecthunter bl-export 3f9a1c2e -o report.json -f json
    """
    configure_logging(LogLevel.ERROR, quiet=True)

    if format is ExportFormat.SQLITE:
        console.print("[bold red]Error:[/bold red] --format sqlite is not supported for bl-export.")
        raise typer.Exit(code=1)

    async def _do_backlink_export() -> None:
        if not database.exists():
            console.print(f"[bold red]Database not found:[/bold red] {database}")
            raise typer.Exit(code=1)

        db = Database(database)
        await db.connect()
        try:
            try:
                resolved_backlink_id = await db.resolve_backlink_check_id(backlink_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            output.parent.mkdir(parents=True, exist_ok=True)
            with console.status(
                f"Exporting backlink check {resolved_backlink_id} to {output} ({format.value})..."
            ):
                rows = db.iter_backlink_results(resolved_backlink_id, confirmed_only=confirmed)
                count = await _write_backlink_export(rows, output, format)
            console.print(f"[bold green]Exported {count} result(s)[/bold green] to {output}")
        finally:
            await db.close()

    asyncio.run(_do_backlink_export())


if __name__ == "__main__":
    app()
