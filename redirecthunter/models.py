"""Core data contracts for RedirectHunter.

Every other module in this package (engine, detector, analyzer, database,
exporter, cli) consumes or produces the models defined here. Keeping them
centralized in one Pydantic-validated module guarantees a single source of
truth for the shape of a scan, a redirect hop, and a final result — no
module is allowed to invent its own ad-hoc dict shape for this data.

All models are immutable-by-convention (validated on construction) and use
Pydantic v2 syntax throughout.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp.

    Centralized so every model uses the exact same clock semantics
    (aware, UTC) — avoids naive-vs-aware datetime bugs when persisting
    to SQLite or serializing to JSON.
    """
    return datetime.now(UTC)


class HTTPMethod(str, Enum):
    """HTTP methods supported by the scanning engine."""

    HEAD = "HEAD"
    GET = "GET"


class RedirectType(str, Enum):
    """Classification of how a redirect was expressed.

    HTTP status-code redirects and content-based redirects (meta refresh,
    JavaScript) are distinguished because they require different detection
    strategies and carry different security implications.
    """

    HTTP_301 = "301_moved_permanently"
    HTTP_302 = "302_found"
    HTTP_303 = "303_see_other"
    HTTP_307 = "307_temporary_redirect"
    HTTP_308 = "308_permanent_redirect"
    META_REFRESH = "meta_refresh"
    JAVASCRIPT = "javascript"
    NONE = "none"


class InputFormat(str, Enum):
    """Supported candidate-URL input file formats."""

    TXT = "txt"
    CSV = "csv"
    JSON = "json"
    SQLITE = "sqlite"


class ExportFormat(str, Enum):
    """Supported result export formats."""

    CSV = "csv"
    JSON = "json"
    SQLITE = "sqlite"


class RedactFormat(str, Enum):
    """Supported output formats for ``redirecthunter redact-target``.

    Distinct from :class:`ExportFormat`: that enum's semantics are "scan
    result export," whereas this one describes a redacted URL list — a
    different artifact with no relationship to a scan's results table.
    """

    TXT = "txt"
    CSV = "csv"
    JSON = "json"
    SQLITE = "sqlite"


class RunStatus(str, Enum):
    """Lifecycle status of a run record (scan, crawl, or backlink-check).

    One shared enum for every run kind -- ``ScanStatus``/``CrawlStatus``/
    ``BacklinkCheckStatus`` were three identical definitions of this same
    thing (see the architecture review of 2026-08-20 / ``CONTEXT.md``'s
    "Run" entry for why they were collapsed).
    """

    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class CloudflareStatus(BaseModel):
    """Classification-only Cloudflare / challenge-page detection result.

    RedirectHunter never attempts to bypass a challenge — this model exists
    purely to *flag* that a target is Cloudflare-protected so the operator
    can make an informed decision (e.g. exclude it from further automated
    testing).
    """

    model_config = ConfigDict(frozen=True)

    is_cloudflare: bool = False
    has_cf_ray: bool = False
    has_cf_cache_status: bool = False
    has_cf_clearance_cookie: bool = False
    has_cdn_cgi_path: bool = False
    is_challenge_page: bool = False
    cf_ray_id: str | None = None


class FingerprintInfo(BaseModel):
    """Server / CDN fingerprint derived from response headers."""

    model_config = ConfigDict(frozen=True)

    server_header: str | None = None
    powered_by_header: str | None = None
    detected_software: str | None = Field(
        default=None,
        description=(
            "Best-effort classification, e.g. 'nginx', 'Apache', 'LiteSpeed', "
            "'IIS', 'Cloudflare', 'CloudFront', 'Fastly', 'Varnish', 'Akamai'."
        ),
    )
    cloudflare: CloudflareStatus = Field(default_factory=CloudflareStatus)


class RedirectHop(BaseModel):
    """A single hop within a redirect chain.

    A chain of N redirects followed by a final 200 response produces N
    ``RedirectHop`` entries plus a terminal ``RedirectResult.final_url``.
    """

    model_config = ConfigDict(frozen=True)

    hop_index: int = Field(ge=0, description="0-based position in the chain.")
    url: str
    status_code: int | None = None
    redirect_type: RedirectType = RedirectType.NONE
    location_header: str | None = None
    server_header: str | None = None
    latency_ms: float = Field(ge=0.0)


class RedirectResult(BaseModel):
    """Complete outcome of validating a single candidate URL.

    This is the primary record persisted to the ``results`` table and
    emitted by every exporter. Field names intentionally mirror the
    "RESULT MODEL" section of the project specification.
    """

    model_config = ConfigDict(frozen=True)

    result_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scan_id: str
    source_url: str = Field(description="Raw URL as read from the input file, template intact.")
    expanded_url: str = Field(description="Source URL with {TARGET} substituted.")
    http_method: HTTPMethod
    status_code: int | None = None
    redirect_type: RedirectType = RedirectType.NONE
    location: str | None = Field(default=None, description="Raw Location header of the first hop.")
    final_url: str | None = None
    body_link: str | None = Field(
        default=None,
        description=(
            "Raw href of the first meaningful <a> link found in the terminal "
            "response's HTML body, if any (e.g. a manual 'click here to continue' "
            "interstitial). Not resolved against the page URL -- may be relative. "
            "None for HEAD requests, non-HTML responses, or bodies with no "
            "qualifying anchor tag."
        ),
    )
    redirect_chain: list[RedirectHop] = Field(default_factory=list)
    hop_count: int = Field(default=0, ge=0)
    server: str | None = None
    content_type: str | None = None
    content_length: int | None = None
    cookies: dict[str, str] = Field(default_factory=dict)
    fingerprint: FingerprintInfo = Field(default_factory=FingerprintInfo)
    alive: bool = Field(description="True if the target responded without a transport-level error.")
    latency_ms: float = Field(ge=0.0, description="Total time for the full redirect chain.")
    error: str | None = Field(default=None, description="Transport/timeout error message, if any.")
    timestamp: datetime = Field(default_factory=_utcnow)

    @field_validator("redirect_chain")
    @classmethod
    def _chain_matches_hop_count(cls, v: list[RedirectHop]) -> list[RedirectHop]:
        return v


class ScanConfig(BaseModel):
    """Fully-resolved configuration for a single scan run.

    Instances are built by :mod:`redirecthunter.config` by layering, in
    increasing priority: built-in defaults -> YAML config file -> CLI flags.
    Nothing downstream (engine, detector, database) reads raw CLI args or
    raw YAML — everything consumes this validated model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_path: Path
    input_format: InputFormat
    input_table: str = Field(
        default="urls", description="Table name to read candidate URLs from (SQLite input only)."
    )
    input_column: str = Field(
        default="url", description="Column name holding candidate URLs (SQLite/CSV input only)."
    )
    target: str | None = Field(
        default=None, description="Replacement value substituted for the {TARGET} placeholder."
    )
    method: HTTPMethod = HTTPMethod.HEAD
    follow_redirects: bool = Field(
        default=True, description="If False, only the first hop is inspected (no chain-following)."
    )
    max_redirects: int = Field(default=10, ge=0, le=50)
    workers: int = Field(default=100, ge=1, le=2000)
    timeout: float = Field(default=10.0, gt=0.0)
    connect_timeout: float = Field(default=5.0, gt=0.0)
    retry: int = Field(default=2, ge=0, le=10)
    retry_backoff: float = Field(default=0.5, ge=0.0)
    rate_limit: float | None = Field(
        default=None, description="Maximum requests per second across all workers. None = unlimited."
    )
    http2: bool = True
    proxy: str | None = None
    user_agent: str = "RedirectHunter/1.0"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    verify_tls: bool = True
    database_path: Path = Field(default=Path("redirecthunter.db"))
    scan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scan_label: str | None = None

    @field_validator("target")
    @classmethod
    def _normalize_target(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        return v


class ScanSummary(BaseModel):
    """Aggregate statistics for a scan, used by the ``stats`` CLI command."""

    model_config = ConfigDict(frozen=True)

    scan_id: str
    label: str | None = None
    status: RunStatus
    total_urls: int = 0
    completed: int = 0
    alive: int = 0
    dead: int = 0
    redirects_found: int = 0
    cloudflare_protected: int = 0
    avg_latency_ms: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def progress_pct(self) -> float:
        """Percentage of ``total_urls`` that have been completed."""
        if self.total_urls == 0:
            return 0.0
        return round((self.completed / self.total_urls) * 100, 2)


class DetectionOutcome(BaseModel):
    """Result of a single redirect-detector plugin run.

    Returned by every plugin in :mod:`redirecthunter.plugins`. ``None`` is
    returned by a plugin's ``detect()`` method (not this model) when no
    redirect is found — this model always represents a *positive* hit.
    """

    model_config = ConfigDict(frozen=True)

    redirect_type: RedirectType
    destination: str = Field(description="Raw, unresolved redirect target as found in source/header.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(description="Which plugin produced this outcome, e.g. 'http_location'.")
    raw_evidence: str | None = Field(
        default=None, description="Snippet of the header/HTML/script that triggered detection."
    )


class CandidateURL(BaseModel):
    """A single row parsed from an input file, prior to {TARGET} expansion."""

    model_config = ConfigDict(frozen=True)

    raw_url: str
    row_metadata: dict[str, Any] = Field(default_factory=dict)


class CrawlSeedMode(str, Enum):
    """How a crawl's starting point(s) are determined.

    ``DOMAIN`` discovers pages by following internal links outward from a
    single seed URL (the "audit my whole site" use case). ``URL_LIST``
    instead seeds the frontier from every row of an input file (TXT/CSV/
    JSON — the same formats :mod:`redirecthunter.loader` already reads for
    ``scan``), auditing exactly those pages (and, if ``follow_links`` is
    also enabled, discovering further pages from them). The two modes
    share one crawl engine; only where the frontier starts differs.
    """

    DOMAIN = "domain"
    URL_LIST = "url_list"


class LinkKind(str, Enum):
    """Whether a link discovered during a crawl points inside or outside scope.

    "Internal" means the link's host is the seed's host (or a subdomain of
    it, or a host explicitly listed in ``CrawlConfig.allowed_domains``) --
    the same host-boundary semantics as
    :func:`redirecthunter.utils.is_external_domain`, not substring matching.
    """

    INTERNAL = "internal"
    EXTERNAL = "external"


class PageIssue(str, Enum):
    """Machine-checkable on-page SEO issue flags attached to a crawled page.

    Deliberately limited to things a single page's own response can prove
    on its own (title/meta/H1 presence and length, its own broken
    outlinks). Cross-page issues that need the *whole* crawl to detect
    (duplicate titles, duplicate meta descriptions, orphan pages) are
    **not** flags on this enum -- they are computed once, after the crawl
    finishes, by :meth:`redirecthunter.database.Database.get_crawl_summary`
    / the ``crawl-stats`` CLI command, via a single aggregate query over
    every page's stored title/description rather than being recomputed
    (and drifting) per-page during the crawl itself.
    """

    MISSING_TITLE = "missing_title"
    TITLE_TOO_SHORT = "title_too_short"
    TITLE_TOO_LONG = "title_too_long"
    MISSING_META_DESCRIPTION = "missing_meta_description"
    META_DESCRIPTION_TOO_LONG = "meta_description_too_long"
    MISSING_H1 = "missing_h1"
    MULTIPLE_H1 = "multiple_h1"


class CrawlLinkResult(BaseModel):
    """Outcome of checking one link discovered on a crawled page.

    Not every discovered link gets a row here: an internal link that's
    within crawl scope (depth/page budget) is promoted straight to a
    ``CrawlPageResult`` fetch instead (see ``Crawler._enqueue_discovered_link``)
    -- a dead internal page shows up as ``status_code >= 400`` on its own
    ``crawl_pages`` row, not as a separate broken-link record here. This
    table exists for everything that *isn't* itself crawled as a page:
    external links, internal links past ``max_depth``/``max_pages``, and
    every occurrence of an internal link *after* its first (the first
    occurrence is the one that got promoted to a page; a page is only
    ever fetched once per crawl no matter how many pages link to it).

    One row is recorded per (source page, target URL) occurrence -- not
    deduplicated across pages -- so "which pages link to this broken URL"
    stays answerable straight from the ``crawl_links`` table for the links
    that do land here. The underlying HTTP check itself *is* deduplicated
    per crawl (see ``Crawler``'s link-status cache): the same target URL
    checked from ten different pages is only ever requested once over the
    network.
    """

    model_config = ConfigDict(frozen=True)

    link_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    crawl_id: str
    source_page_url: str = Field(description="The page the link was found on.")
    target_url: str = Field(description="Resolved, absolute URL the link points to.")
    raw_href: str = Field(description="Original href/src attribute text, unresolved.")
    link_kind: LinkKind
    anchor_text: str | None = Field(default=None, description="Visible text of the <a> tag, if any.")
    rel: str | None = Field(default=None, description="Raw value of the anchor's rel attribute, if any (e.g. \"nofollow\", \"sponsored ugc\").")
    target_attr: str | None = Field(default=None, description="Raw value of the anchor's target attribute, if any (e.g. \"_blank\").")
    status_code: int | None = None
    is_broken: bool = Field(
        description="True if the link returned a 4xx/5xx status or a transport-level error."
    )
    redirected: bool = Field(default=False, description="True if the request followed one or more redirects.")
    final_url: str | None = None
    error: str | None = Field(default=None, description="Transport/timeout error message, if any.")
    latency_ms: float = Field(ge=0.0, default=0.0)
    checked_at: datetime = Field(default_factory=_utcnow)


class CrawlPageResult(BaseModel):
    """Complete outcome of fetching and analyzing one page during a crawl.

    The primary record persisted to the ``crawl_pages`` table -- the
    crawl-mode counterpart to :class:`RedirectResult`, covering the
    additional on-page signals (title, meta description, H1s, outlink
    health) that redirect validation alone doesn't need.
    """

    model_config = ConfigDict(frozen=True)

    page_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    crawl_id: str
    url: str
    depth: int = Field(ge=0, description="0 for a seed URL, N for a page discovered N hops away from one.")
    discovered_from: str | None = Field(default=None, description="The page whose link led here. None for seeds.")
    status_code: int | None = None
    alive: bool = Field(default=False, description="True if the page responded without a transport-level error.")
    redirected: bool = False
    final_url: str | None = None
    content_type: str | None = None
    title: str | None = None
    title_length: int = 0
    meta_description: str | None = None
    meta_description_length: int = 0
    h1_texts: list[str] = Field(default_factory=list)
    h1_count: int = 0
    internal_link_count: int = Field(default=0, description="Count of unique internal link targets found on this page.")
    external_link_count: int = Field(default=0, description="Count of unique external link targets found on this page.")
    word_count: int = 0
    issues: list[PageIssue] = Field(
        default_factory=list,
        description=(
            "On-page issues this page's own response can prove by itself (title/meta/H1). "
            "Whether this page *links to* something broken is answered by joining the "
            "crawl_links table on source_page_url, not stored here -- link checks for a page's "
            "outlinks can complete well after the page itself has already been persisted."
        ),
    )
    latency_ms: float = Field(ge=0.0, default=0.0)
    error: str | None = None
    timestamp: datetime = Field(default_factory=_utcnow)


class CrawlConfig(BaseModel):
    """Fully-resolved configuration for a single crawl run.

    The crawl-mode counterpart to :class:`ScanConfig`. Deliberately a
    separate model rather than bolted onto ``ScanConfig`` with a pile of
    ``| None`` crawl-only fields: a crawl's frontier (BFS over
    dynamically-discovered links, bounded by depth/page count) is a
    genuinely different shape of work from scanning a fixed candidate
    list, and every field here is meaningful for every crawl -- unlike
    ``ScanConfig`` fields that would only apply to one of two modes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    crawl_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    crawl_label: str | None = None
    seed_mode: CrawlSeedMode
    seed_url: str | None = Field(default=None, description="Starting URL for CrawlSeedMode.DOMAIN.")
    seed_input_path: Path | None = Field(
        default=None, description="Input file (TXT/CSV/JSON) for CrawlSeedMode.URL_LIST."
    )
    seed_input_format: InputFormat | None = None
    seed_input_column: str = "url"
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Extra hostnames (besides the seed's own) treated as 'internal' scope.",
    )
    max_depth: int = Field(default=3, ge=0, le=20)
    max_pages: int = Field(default=500, ge=1, le=200_000)
    follow_links: bool = Field(
        default=True,
        description=(
            "If False, only the seed URL(s) themselves are fetched/audited -- links found on "
            "them are still status-checked (see check_external_links) but never crawled further."
        ),
    )
    check_external_links: bool = Field(
        default=True, description="If False, external links are counted but never requested."
    )
    include_query_string: bool = Field(
        default=True,
        description="If False, URLs differing only by query string are treated as the same page.",
    )
    workers: int = Field(default=20, ge=1, le=500)
    timeout: float = Field(default=10.0, gt=0.0)
    connect_timeout: float = Field(default=5.0, gt=0.0)
    retry: int = Field(default=1, ge=0, le=10)
    retry_backoff: float = Field(default=0.5, ge=0.0)
    rate_limit: float | None = None
    http2: bool = True
    proxy: str | None = None
    user_agent: str = "RedirectHunter-Crawler/1.0"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    verify_tls: bool = True
    title_min_length: int = Field(default=10, ge=1)
    title_max_length: int = Field(default=60, ge=1)
    meta_description_max_length: int = Field(default=160, ge=1)
    database_path: Path = Field(default=Path("redirecthunter.db"))

    @field_validator("seed_url")
    @classmethod
    def _normalize_seed_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class CrawlSummary(BaseModel):
    """Aggregate statistics for a crawl, used by the ``crawl-stats`` CLI command."""

    model_config = ConfigDict(frozen=True)

    crawl_id: str
    label: str | None = None
    status: RunStatus
    seed_mode: CrawlSeedMode
    pages_crawled: int = 0
    pages_alive: int = 0
    pages_dead: int = 0
    links_checked: int = 0
    broken_links: int = 0
    broken_internal_links: int = 0
    broken_external_links: int = 0
    pages_missing_title: int = 0
    pages_duplicate_title: int = 0
    pages_missing_meta_description: int = 0
    pages_duplicate_meta_description: int = 0
    pages_missing_h1: int = 0
    pages_multiple_h1: int = 0
    avg_latency_ms: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BacklinkCheckConfig(BaseModel):
    """Fully-resolved configuration for a single ``bl-check`` run.

    The backlink-check-mode counterpart to :class:`ScanConfig`/:class:`CrawlConfig`.
    One domain, one input file per run (multi-domain-per-run is explicitly
    out of scope -- see ``.scratch/backlink-check-cli/spec.md``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    backlink_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: str
    input_path: Path
    input_format: InputFormat | None = None
    allow_subdomains: bool = True
    check_indirect: bool = True
    concurrency: int = Field(default=8, ge=1, le=500)
    timeout: float = Field(default=15.0, gt=0.0)
    user_agent: str = "Mozilla/5.0 (compatible; BacklinkChecker/1.0; +https://example.org/bot)"
    #: Extra request headers, e.g. a manually-obtained `Cookie:` value for
    #: pages that only serve their real content to a logged-in session
    #: (see `looks_like_login_wall`). This is *not* a login flow -- there's
    #: no username/password/form-submission here, just headers attached to
    #: every request, the same "bring your own session cookie" model
    #: `scan`/`crawl`'s `--header` already use. Applied in both httpx mode
    #: (merged into the `httpx.AsyncClient` default headers) and
    #: `--browser` mode (passed to the Playwright `BrowserContext`).
    extra_headers: dict[str, str] | None = None
    #: Per-domain-scoped headers, e.g. a different session cookie for each
    #: of several login-walled platforms in one multi-platform backlink
    #: list -- see `cli.py`'s `-H "domain.com|Name: Value"` syntax and
    #: `backlink.resolve_domain_headers`. Keys are normalized domains;
    #: unlike `extra_headers`, these are never sent to URLs on other hosts.
    domain_headers: dict[str, dict[str, str]] | None = None
    database_path: Path = Field(default=Path("redirecthunter.db"))
    label: str | None = None
    #: Render with Playwright (needs the `redirecthunter[js]` extra)
    #: instead of a plain httpx GET -- for pages whose links are added by
    #: client-side JS after load. `concurrency` means *browser tabs* in
    #: this mode, not HTTP connections -- keep it much lower (the CLI
    #: picks a lower default automatically when this is set and `-c`
    #: isn't given explicitly).
    browser: bool = False
    #: Show the real browser window instead of running headless. Only
    #: meaningful when `browser=True`; a debugging convenience, not
    #: something a scheduled/CI run would use.
    headed: bool = False
    #: Seconds to wait for the initial Playwright navigation. Only used
    #: when `browser=True` (`timeout` above is httpx-mode's equivalent).
    nav_timeout: float = Field(default=30.0, gt=0.0)
    #: Seconds to wait for the page to go network-idle after load, so a
    #: SPA's own JS has time to fetch data and hydrate the DOM before the
    #: page is inspected. Only used when `browser=True`.
    render_wait: float = Field(default=8.0, gt=0.0)
    #: Block image/media/font requests during a browser-mode run --
    #: faster page loads, and irrelevant to whether a link exists in the
    #: DOM. Only used when `browser=True`.
    block_resources: bool = True


class BacklinkCheckSummary(BaseModel):
    """Aggregate statistics for a ``bl-check`` run, used by the ``bl-stats`` CLI command."""

    model_config = ConfigDict(frozen=True)

    backlink_id: str
    domain: str
    label: str | None = None
    status: RunStatus
    total_urls: int = 0
    confirmed: int = 0
    indirect: int = 0
    text_mention_only: int = 0
    not_found: int = 0
    blocked: int = 0
    requires_login: int = 0
    error: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BacklinkChainConfig(BaseModel):
    """Fully-resolved configuration for a single ``bl-chain`` run.

    The tiered-verification counterpart to :class:`BacklinkCheckConfig`:
    an ordered list of input files (``tier_paths``) instead of one, and a
    single root ``domain`` (tier 1's target only -- middle tiers derive
    their own default target set at run time from the previous tier's
    input URLs, see ``cli.py``'s ``bl_chain`` command). v1 applies one
    shared set of the fields below to every tier in the chain -- see
    ``.scratch/bl-chain/spec.md``'s "Out of Scope" for why per-tier
    overrides of these aren't supported yet.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chain_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: str
    #: Tier order as given on the command line -- never re-sorted or
    #: inferred from filenames (see spec's user story 4).
    tier_paths: list[Path]
    #: Off by default: tier N+1's derived target set is built from *all*
    #: of tier N's input URLs. When True, only tier N rows where
    #: ``match_found`` was true contribute to that derived set.
    require_confirmed_parent: bool = False
    allow_subdomains: bool = True
    check_indirect: bool = True
    concurrency: int = Field(default=8, ge=1, le=500)
    timeout: float = Field(default=15.0, gt=0.0)
    user_agent: str = "Mozilla/5.0 (compatible; BacklinkChecker/1.0; +https://example.org/bot)"
    extra_headers: dict[str, str] | None = None
    domain_headers: dict[str, dict[str, str]] | None = None
    database_path: Path = Field(default=Path("redirecthunter.db"))
    label: str | None = None
    browser: bool = False
    headed: bool = False
    nav_timeout: float = Field(default=30.0, gt=0.0)
    render_wait: float = Field(default=8.0, gt=0.0)
    block_resources: bool = True


class BacklinkChainSummary(BaseModel):
    """Aggregate view of a ``bl-chain`` run, used by the ``bl-chain-stats`` CLI command."""

    model_config = ConfigDict(frozen=True)

    chain_id: str
    domain: str
    label: str | None = None
    status: RunStatus
    #: Per-tier summaries, in tier order -- each element is an ordinary
    #: ``BacklinkCheckSummary`` for that tier's own ``backlink_checks`` row.
    tiers: list[BacklinkCheckSummary] = Field(default_factory=list)


__all__ = [
    "HTTPMethod",
    "RedirectType",
    "InputFormat",
    "ExportFormat",
    "RedactFormat",
    "RunStatus",
    "CloudflareStatus",
    "FingerprintInfo",
    "RedirectHop",
    "RedirectResult",
    "ScanConfig",
    "ScanSummary",
    "DetectionOutcome",
    "CandidateURL",
    "CrawlSeedMode",
    "LinkKind",
    "PageIssue",
    "CrawlLinkResult",
    "CrawlPageResult",
    "CrawlConfig",
    "CrawlSummary",
    "BacklinkCheckConfig",
    "BacklinkCheckSummary",
    "BacklinkChainConfig",
    "BacklinkChainSummary",
]
