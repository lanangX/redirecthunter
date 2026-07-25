"""RedirectHunter command-line interface.

Wires together every other module into five Typer commands:

    - ``scan``   — run a new scan against a candidate-URL input file.
    - ``resume`` — continue an interrupted scan from where it left off.
    - ``stats``  — show aggregate statistics for one or all recorded scans.
    - ``export`` — export a scan's results to CSV, JSON, or SQLite.
    - ``show``   — display individual results in a Rich table, with filters.

This module contains no scanning, parsing, or persistence logic of its
own — it only translates CLI flags into a
:class:`~redirecthunter.models.ScanConfig`, drives the
:class:`~redirecthunter.engine.Engine`, and renders progress/output with
Rich.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
import typer
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

from redirecthunter.config import ConfigError, build_scan_config
from redirecthunter.database import Database, DatabaseError
from redirecthunter.engine import Engine
from redirecthunter.exporter import Exporter, ExportError
from redirecthunter.loader import LoaderError, count_candidates, load_candidates
from redirecthunter.logger import LogLevel, configure_logging, console, get_logger
from redirecthunter.models import (
    ExportFormat,
    HTTPMethod,
    InputFormat,
    RedirectResult,
    RedirectType,
    ScanStatus,
)
from redirecthunter.utils import is_external_domain, resolve_relative_url

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


async def _run_scan(config, *, resume: bool) -> None:  # noqa: ANN001 - ScanConfig
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

        def remaining_candidates():
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
                await database.update_scan_status(config.scan_id, ScanStatus.COMPLETED, finished=True)
            except (KeyboardInterrupt, asyncio.CancelledError):
                await database.update_scan_status(config.scan_id, ScanStatus.INTERRUPTED)
                console.print(
                    f"\n[yellow]Scan interrupted.[/yellow] Resume with: "
                    f"redirecthunter resume {config.scan_id} --database {config.database_path}"
                )
                raise
            except Exception:
                await database.update_scan_status(config.scan_id, ScanStatus.FAILED, finished=True)
                raise

        summary = await database.get_scan_summary(config.scan_id)
    finally:
        await database.close()

    if summary is not None:
        _print_summary_table(summary)


def _print_summary_table(summary) -> None:  # noqa: ANN001 - ScanSummary
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
        help="HTTP method (HEAD or GET). GET is required to detect meta-refresh/JS redirects.",
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
) -> None:
    """Export a scan's results to CSV, JSON, or a standalone SQLite file.

    Examples:

        redirecthunter export 3f9a1c2e-... results.csv

        redirecthunter export 3f9a1c2e-... results.json --format json

        redirecthunter export 3f9a1c2e-... results.db --format sqlite
    """
    configure_logging(LogLevel.ERROR, quiet=True)

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
                    count = await exporter.export(resolved_scan_id, output, format)
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
) -> None:
    """Display individual results for a scan in a Rich table, with optional filters.

    Examples:

        redirecthunter show 3f9a1c2e-...

        redirecthunter show 3f9a1c2e-... --redirects-only --limit 100

        redirecthunter show 3f9a1c2e   # a short, unambiguous prefix also works
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
                resolved_scan_id = await db.resolve_scan_id(scan_id)
            except DatabaseError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                raise typer.Exit(code=1) from None

            table = Table(title=f"Results — {resolved_scan_id}")
            for column in ("Source URL", "Status", "Type", "Final URL", "Hops", "Server", "CF", "Alive", "Latency"):
                table.add_column(column, overflow="fold")

            shown = 0
            async for result in db.iter_results(resolved_scan_id):
                if alive_only and not result.alive:
                    continue
                if redirects_only and result.redirect_type.value == "none":
                    continue
                if cloudflare_only and not result.fingerprint.cloudflare.is_cloudflare:
                    continue
                if shown >= limit:
                    break

                table.add_row(
                    result.source_url,
                    str(result.status_code) if result.status_code is not None else "-",
                    result.redirect_type.value,
                    result.final_url or "-",
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
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Save results as a plain link list (one URL per line) to this file, instead of printing a table.",
        ),
    ] = None,
    include_source: Annotated[
        bool,
        typer.Option(
            "--include-source",
            help="With --output, also include the source URL: 'source_url -> final_url' instead of just final_url.",
        ),
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Maximum rows shown in the terminal table.")] = 50,
) -> None:
    """Find redirects that point outside a given domain (e.g. off-site redirect targets).

    Without --output, prints a Rich table (like `show`). With --output,
    writes a plain list of links -- one URL per line, nothing else -- to
    the given file. This is the check most relevant for open-redirect
    auditing: does a redirect endpoint actually send visitors somewhere
    outside the intended/authorized domain?

    Examples:

        redirecthunter find 3f9a1c2e

        redirecthunter find 3f9a1c2e --output external_redirects.txt

        redirecthunter find 3f9a1c2e --domain example.org --output out.txt --include-source
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
            async for result in db.iter_results(resolved_scan_id):
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

                if is_external_domain(destination, domain):
                    matches.append((result, destination))

            if output is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("w", encoding="utf-8") as fh:
                    for r, destination in matches:
                        if include_source:
                            fh.write(f"{r.source_url} -> {destination}\n")
                        else:
                            fh.write(f"{destination}\n")
                console.print(
                    f"[bold green]{len(matches)} redirect(s)[/bold green] to outside '{domain}' "
                    f"saved to {output}"
                )
                return

            table = Table(title=f"Redirects outside '{domain}' — {resolved_scan_id}")
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


if __name__ == "__main__":
    app()
