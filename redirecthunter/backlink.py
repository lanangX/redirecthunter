"""redirecthunter/backlink.py — shared backlink-matching logic.

This module owns the hostname/pattern-matching helpers and the
`BacklinkResult` model, plus two check implementations answering a
different question from `scan`/`crawl`: does a page's rendered HTML
genuinely contain an outbound link to a target domain, as opposed to
whether a URL *redirects to* a target.

`check_one`/`run_backlink_checks` fetch with `httpx` (static HTML only).
`check_one_browser`/`run_backlink_checks_browser` render with Playwright
first (`redirecthunter[js]` extra, lazily imported so the base package
never requires it) -- for pages whose links are added by client-side
JS after load. Both feed the CLI's `bl-check --browser` command; see
`MEMORY.md` for why this became one CLI command with a mode flag
instead of the two standalone root scripts it replaced.

`BacklinkResult` is a Pydantic model (not a dataclass) so it can flow
through the same database-serialization path `CrawlPageResult`/
`CrawlLinkResult` already use.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from redirecthunter.engine import RateLimiter

if TYPE_CHECKING:
    # Type-checking only -- never imported at runtime, so the base package
    # (and `mypy`, via `ignore_missing_imports`) never needs `playwright`
    # installed just to import this module. `run_backlink_checks_browser`
    # does the real (lazy, function-body) import when browser mode is
    # actually used.
    from playwright.async_api import Browser, BrowserContext, Page

# --------------------------------------------------------------------------
# Domain normalization / matching — this is the false-positive guard.
#
# We NEVER do a raw substring search for the domain across the whole page
# (that's what causes false positives). Every href is parsed into an actual
# hostname and compared as a *whole hostname*, not a fragment. This alone
# rules out the classic false-positive shapes:
#
#   - "notmedilana.id"            (different, longer hostname)
#   - "medilana.id.otherhost.com" (different, longer hostname)
#   - "somemedilana.id.tld"       (different, longer hostname)
# --------------------------------------------------------------------------

_NON_NAVIGABLE_PREFIXES = ("#", "javascript:", "mailto:", "tel:", "data:")


def normalize_domain(domain: str) -> str:
    """Lowercase, strip scheme/www./trailing dot/trailing slash from a domain."""
    d = domain.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0]
    d = d.rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    return d


def normalize_hostname(hostname: str) -> str:
    """Same normalization as normalize_domain, applied to a parsed hostname."""
    h = hostname.strip().lower().rstrip(".")
    if h.startswith("www."):
        h = h[4:]
    return h


def hostname_matches(hostname: str, target_domain: str, *, allow_subdomains: bool) -> bool:
    """True if `hostname` IS `target_domain` (or a subdomain of it, if allowed).

    Exact-hostname comparison, not substring — this is the core false-positive
    guard. "blog.medilana.id" matches as a subdomain; "medilana.id.evil.com"
    and "notmedilana.id" never match at all.
    """
    h = normalize_hostname(hostname)
    t = normalize_domain(target_domain)
    if h == t:
        return True
    return bool(allow_subdomains and h.endswith("." + t))


def hostname_matches_any(
    hostname: str, target_domains: frozenset[str], *, allow_subdomains: bool
) -> str | None:
    """Like `hostname_matches`, but against a *set* of target domains.

    Returns the specific target domain (as given in `target_domains`,
    unnormalized) that matched, or `None` if none did. Used by `bl-chain`,
    where a middle tier's target is "any host in the previous tier's
    input list," not one domain -- `bl-check` itself still only ever
    passes a one-element set, so its own match_type/behavior is unchanged.

    Every element of `target_domains` gets the same false-positive-guarded
    exact-hostname-or-subdomain comparison `hostname_matches` already does
    -- this is not a substring/union-regex shortcut, it's N individual
    exact comparisons.
    """
    h = normalize_hostname(hostname)
    for target in target_domains:
        t = normalize_domain(target)
        if h == t or (allow_subdomains and h.endswith("." + t)):
            return target
    return None


def extract_hostname(href: str, base_url: str) -> str | None:
    """Resolve `href` against `base_url` and return its hostname, or None.

    Handles:
      - normal absolute/relative hrefs        -> urljoin + urlparse
      - protocol-relative hrefs ("//host/..") -> urljoin handles this natively
      - bare, scheme-less domains written directly in href
        (e.g. href="medilana.id" or href="medilana.id/artikel") which
        urlparse would otherwise misread as a relative *path*, not a host.
    """
    href = href.strip()
    if not href:
        return None

    # Case: no scheme, no leading slash, but looks like "domain.tld[/path]".
    # A real relative path on the *same* site would virtually never look
    # like this in practice for a bare top-level anchor, and browsers do
    # in fact treat `<a href="example.com">` as pointing at example.com's
    # own DNS root in many hand-written HTML/forum contexts. We only apply
    # this heuristic when the first path segment contains a dot and no
    # slash before it, keeping normal relative links ("about", "../x")
    # from being misclassified.
    if "://" not in href and not href.startswith("//") and not href.startswith("/"):
        first_segment = href.split("/", 1)[0]
        if "." in first_segment and " " not in first_segment and not first_segment.startswith("."):
            href = "//" + href  # promote to protocol-relative, urljoin resolves it

    resolved = urljoin(base_url, href)
    parsed = urlparse(resolved)
    return parsed.hostname


# --------------------------------------------------------------------------
# Weak-signal: plain-text mention of the domain (not inside any <a> href).
# Reported separately and never counted as a confirmed backlink, since a
# visible text mention proves nothing about where a link (if any) points.
# --------------------------------------------------------------------------

def build_text_mention_pattern(target_domain: str) -> re.Pattern[str]:
    escaped = re.escape(normalize_domain(target_domain))
    # boundary-aware: reject "notmedilana.id" / "medilana.id.evil.com"
    return re.compile(rf"(?<![a-z0-9\-]){escaped}(?![a-z0-9\-.])", re.IGNORECASE)


def build_text_mention_pattern_any(target_domains: frozenset[str]) -> re.Pattern[str]:
    """Like `build_text_mention_pattern`, but matching any of several domains.

    One boundary-aware alternation over every normalized target -- same
    false-positive guard as the single-domain version, just applied to
    N domains in one compiled pattern instead of one.
    """
    escaped = [re.escape(normalize_domain(target)) for target in target_domains]
    alternation = "|".join(escaped)
    return re.compile(rf"(?<![a-z0-9\-])(?:{alternation})(?![a-z0-9\-.])", re.IGNORECASE)


def _extract_robots_meta(tree: HTMLParser) -> str | None:
    """Pull the `content` of `<meta name="robots" content="...">` from a parsed page.

    Shared by `check_one` (httpx, static HTML) and `check_one_browser`
    (Playwright, rendered HTML) so both report this the same way. Only
    the on-page meta tag -- the `X-Robots-Tag` response header is a
    separate, independent signal each caller reads straight off its own
    response object (see `BacklinkResult.robots_header`'s docstring for
    why the two aren't merged into one field).
    """
    for meta in tree.css("meta"):
        name = (meta.attributes.get("name") or "").strip().lower()
        if name == "robots":
            return meta.attributes.get("content")
    return None


# --------------------------------------------------------------------------
# Cloudflare / bot-challenge detection — pages that never actually serve
# their real body to an automated fetcher. Reported as a distinct status so
# "no link found" (real signal) is never confused with "couldn't see the
# real page at all" (false negative risk, not a true absence of the link).
# --------------------------------------------------------------------------

_CHALLENGE_MARKERS = (
    "just a moment",
    "attention required",
    "checking your browser",
    "verify you are human",
    "cf-browser-verification",
)


def looks_like_challenge_page(status_code: int, title: str | None, headers: Mapping[str, str]) -> bool:
    if status_code in (403, 503):
        low_title = (title or "").lower()
        if any(marker in low_title for marker in _CHALLENGE_MARKERS):
            return True
    return bool(headers.get("cf-mitigated") == "challenge")


# --------------------------------------------------------------------------
# Non-standard anti-bot status codes. LinkedIn deliberately returns HTTP 999
# for requests it fingerprints as automated scraping — it is not a real
# "not found", it means the server refused to serve any real content at
# all, so it must never be lumped in with a genuine absence of the link.
# --------------------------------------------------------------------------

_ANTI_BOT_STATUS_CODES = (999,)


def looks_like_bot_block_status(status_code: int) -> bool:
    return status_code in _ANTI_BOT_STATUS_CODES


def resolve_domain_headers(
    url: str, domain_headers: Mapping[str, Mapping[str, str]] | None
) -> dict[str, str] | None:
    """Pick the header set (if any) whose scoping domain matches ``url``'s own host.

    ``domain_headers`` is keyed by normalized domain (see `_parse_scoped_headers`
    in `cli.py`) -- e.g. checking against multiple different login-walled
    platforms in one `bl-check` run, each with its own session cookie, without
    leaking any of them to the rest of the (usually much larger) URL list.
    Subdomains match their parent the same way `hostname_matches` already does
    everywhere else in this module. First matching entry wins if more than one
    domain key happens to match (longest/most specific is not disambiguated --
    keep scoped domains non-overlapping in practice).
    """
    if not domain_headers:
        return None
    host = urlparse(url).hostname
    if not host:
        return None
    for domain, headers in domain_headers.items():
        if hostname_matches(host, domain, allow_subdomains=True):
            return dict(headers)
    return None


def resolve_account_headers(
    url: str,
    per_url_account_id: Mapping[str, str] | None,
    account_headers: Mapping[str, Mapping[str, str]] | None,
) -> dict[str, str] | None:
    """Look up the registered header set (if any) for ``url``'s own ``account_id``.

    ``per_url_account_id`` maps a candidate's exact ``raw_url`` to the
    ``account_id`` parsed from its input row (TXT ``account_id|URL``
    prefix, CSV ``account_id`` column, or JSON ``"account_id"`` key --
    see ``loader.py``). ``account_headers`` is the ``--accounts-file``
    registry (``account_id`` -> its header dict, built by
    ``cli.py``'s ``_parse_account_headers``).

    Returns ``None`` (not an empty dict) when ``url`` has no
    ``account_id`` at all, so callers can tell "this URL is anonymous/
    public" apart from "this URL's account is registered with zero
    headers" (an explicit, documented no-op -- see
    ``_parse_account_headers``'s docstring). A referenced ``account_id``
    that is entirely absent from the registry is a configuration error
    the CLI validates and refuses to run with (see
    ``cli.py``'s ``_validate_account_references``) -- by the time this
    function runs, every ``account_id`` in ``per_url_account_id`` is
    guaranteed to be a key of ``account_headers``, so a missing key here
    is never silently treated as "no headers."
    """
    if not per_url_account_id or not account_headers:
        return None
    account_id = per_url_account_id.get(url)
    if account_id is None:
        return None
    headers = account_headers.get(account_id)
    return dict(headers) if headers else None


def resolve_effective_headers(
    url: str,
    domain_headers: Mapping[str, Mapping[str, str]] | None,
    per_url_account_id: Mapping[str, str] | None = None,
    account_headers: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, str] | None:
    """Merge domain-scoped and account-scoped headers for one request, account wins ties.

    Priority (lowest to highest, later updates win on overlapping header
    names): global headers (already baked into the client's/context's own
    default headers, not this function's concern) -> ``domain_headers``
    (``resolve_domain_headers``) -> account-specific headers
    (``resolve_account_headers``). This is the one place both
    `run_backlink_checks` (httpx) and `run_backlink_checks_browser`
    (Playwright) go through to build a request's/page's extra headers, so
    the priority order only needs to be correct in one spot.

    A per-row account selector is deliberately the *most* specific/highest-
    priority layer: an operator who tagged one row with ``account_001``
    wants that exact session used for that exact row, even if the row's
    own host also happens to carry an unrelated domain-scoped cookie from
    the same run's ``-H``/``--headers-file``.

    Returns ``None`` (not ``{}``) when neither layer contributes anything,
    matching ``resolve_domain_headers``'s existing "no override" contract.
    """
    merged: dict[str, str] = {}
    domain_specific = resolve_domain_headers(url, domain_headers)
    if domain_specific:
        merged.update(domain_specific)
    account_specific = resolve_account_headers(url, per_url_account_id, account_headers)
    if account_specific:
        merged.update(account_specific)
    return merged or None


# --------------------------------------------------------------------------
# Login-wall detection — platforms (Facebook chief among them) that redirect
# an unauthenticated request to a login page instead of serving the profile.
# The login page's HTML is not the target page's real content, so it must
# never be scanned for anchors/text-mentions -- doing so risks a false
# "text_mention_only" from the login page itself (e.g. the original URL
# echoed back in a `next=` param) when in fact nothing about the real page
# was ever observed.
# --------------------------------------------------------------------------

_LOGIN_WALL_PATH_MARKERS = ("/login", "/authwall", "/accounts/login", "/signin", "/checkpoint")
_LOGIN_WALL_QUERY_MARKERS = ("next=", "return", "redirect", "continue")


def looks_like_login_wall(final_url: str) -> bool:
    """True if `final_url` looks like a "log in to continue" redirect target.

    Requires both a login-shaped path *and* a query string that carries the
    original destination forward (e.g. `?next=...`) -- that combination is
    what distinguishes a genuine authwall redirect from, say, a page that
    simply has "/login" somewhere in an unrelated path.
    """
    parsed = urlparse(final_url)
    path = parsed.path.lower()
    query = parsed.query.lower()
    if not any(marker in path for marker in _LOGIN_WALL_PATH_MARKERS):
        return False
    return any(marker in query for marker in _LOGIN_WALL_QUERY_MARKERS)


class BacklinkResult(BaseModel):
    """Result of checking one URL for a genuine outbound link to a target domain.

    Pydantic (not a dataclass) so this can flow through the same
    database-serialization path `CrawlPageResult`/`CrawlLinkResult` use.
    """

    source_url: str
    final_url: str | None = None
    status_code: int | None = None
    match_found: bool = False
    # anchor | subdomain_anchor | final_url_is_target | indirect_query |
    # text_mention_only | not_found
    match_type: str = "not_found"
    matched_href: str | None = None
    rel: str | None = None
    target: str | None = None
    #: Which member of the run's target-domain set this result actually
    #: matched (unnormalized, as given by the caller). Always equal to
    #: the run's single domain in plain `bl-check` mode; meaningful once
    #: `bl-chain` passes a real multi-member set (a middle tier's target
    #: is "any host in the previous tier's input list," not one domain).
    #: `None` when `match_found` is `False`.
    matched_target: str | None = None
    blocked: bool = False
    requires_login: bool = False
    error: str | None = None
    text_mentions: int = 0
    #: Raw `content` of `<meta name="robots" content="...">`, if present.
    robots_meta: str | None = None
    #: Raw value of the `X-Robots-Tag` response header, if present. Can
    #: disagree with `robots_meta` (per Google's own spec, the header
    #: takes precedence) -- both are captured rather than merged into
    #: one field so that disagreement is visible, not silently resolved.
    robots_header: str | None = None
    notes: list[str] = Field(default_factory=list)


#: Column order for CSV/JSON export of `BacklinkResult` rows. One shared
#: source of truth for the CLI's `bl-export` (previously also shared with
#: `backlink_checker.py`'s `write_csv()`, before that script was folded
#: into `bl-check` -- see CONTEXT.md's "Run" entry / MEMORY.md).
BACKLINK_RESULT_COLUMNS: tuple[str, ...] = (
    "source_url",
    "final_url",
    "status_code",
    "match_found",
    "match_type",
    "matched_href",
    "rel",
    "target",
    "matched_target",
    "blocked",
    "requires_login",
    "text_mentions",
    "robots_meta",
    "robots_header",
    "error",
    "notes",
)


async def check_one(
    client: httpx.AsyncClient,
    url: str,
    target_domains: frozenset[str],
    *,
    allow_subdomains: bool,
    check_indirect: bool,
    request_headers: Mapping[str, str] | None = None,
) -> BacklinkResult:
    result = BacklinkResult(source_url=url)
    try:
        resp = await client.get(url, headers=request_headers)
    except httpx.HTTPError as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    result.final_url = str(resp.url)
    result.status_code = resp.status_code
    result.robots_header = resp.headers.get("x-robots-tag")

    if looks_like_bot_block_status(resp.status_code):
        result.blocked = True
        result.notes.append(
            f"HTTP {resp.status_code} — anti-scraping block (e.g. LinkedIn), not a real 'not found'; "
            "the server refused to serve any content to an automated request"
        )
        return result

    if resp.status_code >= 400:
        result.notes.append(f"HTTP {resp.status_code}")

    if looks_like_login_wall(result.final_url):
        result.requires_login = True
        result.notes.append(
            "redirected to a login page instead of the real content — page requires authentication "
            "to view, so no real conclusion about the backlink can be drawn from this response"
        )
        return result

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type and resp.text.strip()[:1] != "<":
        result.notes.append(f"non-HTML response ({content_type or 'unknown content-type'})")
        return result

    tree = HTMLParser(resp.text)
    result.robots_meta = _extract_robots_meta(tree)
    _indirect_pattern = build_text_mention_pattern_any(target_domains)

    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else None
    if looks_like_challenge_page(resp.status_code, title, resp.headers):
        result.blocked = True
        result.notes.append("looks like a bot-challenge / interstitial page, not the real content")
        return result

    # --- Strongest possible signal: the request itself landed on one of the
    # target domains (e.g. a short-link redirect resolving straight to the
    # target). This is verified independently of the page's own HTML/anchors,
    # so it takes priority over the anchor scan below.
    final_hostname = urlparse(result.final_url).hostname
    final_match = (
        hostname_matches_any(final_hostname, target_domains, allow_subdomains=allow_subdomains)
        if final_hostname
        else None
    )
    if final_match is not None:
        result.match_found = True
        result.match_type = "final_url_is_target"
        result.matched_target = final_match
        result.notes.append(
            "the request landed directly on a target domain (e.g. a short-link redirect) — "
            "strongest possible signal, independent of any <a href> on the page"
        )
        return result

    # --- Primary pass: real <a href> hostname match ------------------------
    indirect_candidate: tuple[str, str | None, str | None, str] | None = None
    for anchor in tree.css("a"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        href = href.strip()
        if not href or href.lower().startswith(_NON_NAVIGABLE_PREFIXES):
            continue

        hostname = extract_hostname(href, result.final_url)
        anchor_match = (
            hostname_matches_any(hostname, target_domains, allow_subdomains=allow_subdomains)
            if hostname
            else None
        )
        if anchor_match is not None:
            is_exact = normalize_hostname(hostname) == normalize_domain(anchor_match)  # type: ignore[arg-type]
            result.match_found = True
            result.match_type = "anchor" if is_exact else "subdomain_anchor"
            result.matched_href = href
            result.matched_target = anchor_match
            result.rel = anchor.attributes.get("rel")
            result.target = anchor.attributes.get("target")
            return result

        # Secondary, lower-confidence signal: domain shows up somewhere
        # *inside* the href (e.g. a tracker/redirector URL that embeds the
        # real target in a query string) even though the href's own
        # hostname is a third-party redirector, not the target itself.
        #
        # Boundary-aware, NOT a naive substring `in` check — a naive check
        # would itself be a false-positive source here, matching
        # "notmedilana.id" or "medilana.id.evil-tracker.com" as if they
        # embedded our domain, when they are simply different hostnames
        # that happen to contain our domain as a text fragment.
        if check_indirect and indirect_candidate is None:
            indirect_match = _indirect_pattern.search(href)
            if indirect_match:
                matched_text = indirect_match.group(0).lower()
                matched_domain = next(
                    (t for t in target_domains if normalize_domain(t) == matched_text), matched_text
                )
                indirect_candidate = (
                    href,
                    anchor.attributes.get("rel"),
                    anchor.attributes.get("target"),
                    matched_domain,
                )

    if indirect_candidate is not None:
        result.match_found = True
        result.match_type = "indirect_query"
        result.matched_href = indirect_candidate[0]
        result.rel = indirect_candidate[1]
        result.target = indirect_candidate[2]
        result.matched_target = indirect_candidate[3]
        result.notes.append(
            "domain only found embedded inside another host's URL (e.g. a redirector/tracker) — "
            "verify manually, this is a weaker signal than a direct anchor"
        )
        return result

    # --- Fallback: plain-text mention, not inside any href -----------------
    body_text = tree.body.text(separator=" ", strip=True) if tree.body else ""
    mentions = _indirect_pattern.findall(body_text)
    if mentions:
        result.text_mentions = len(mentions)
        result.match_type = "text_mention_only"
        result.notes.append(
            "domain appears as plain text on the page but not inside any <a href> — "
            "not a real backlink, could be unlinked, JS-rendered, or removed"
        )

    return result


async def run_backlink_checks(
    urls: Sequence[str],
    target_domains: frozenset[str],
    *,
    concurrency: int,
    timeout: float,  # noqa: ASYNC109 - an httpx per-request timeout value, not a cancellation scope
    allow_subdomains: bool,
    check_indirect: bool,
    user_agent: str,
    extra_headers: Mapping[str, str] | None = None,
    domain_headers: Mapping[str, Mapping[str, str]] | None = None,
    per_url_targets: Mapping[str, frozenset[str]] | None = None,
    per_url_account_id: Mapping[str, str] | None = None,
    account_headers: Mapping[str, Mapping[str, str]] | None = None,
    on_result: Callable[[BacklinkResult], Awaitable[None]],
) -> None:
    """Check every URL for a genuine outbound link to one of ``target_domains``.

    ``target_domains`` is the run's *default* target set -- one element in
    plain `bl-check` mode, or the full derived set of a `bl-chain` tier.
    ``per_url_targets``, when given, overrides this on a per-URL basis
    (e.g. a row-level ``|target`` pin from the input file) -- resolved
    once here, before dispatch, so `check_one` itself only ever sees a
    plain `frozenset[str]` and never needs to know an override concept
    exists.

    ``extra_headers`` (e.g. a manually-obtained ``Cookie:`` value) is
    merged into every request's headers, on top of ``User-Agent`` -- this
    is how a page behind a login wall (see ``looks_like_login_wall``) can
    be checked with a real, already-authenticated session. There is no
    username/password/login-form flow here: RedirectHunter never logs
    in on your behalf. You authenticate manually in a real browser once,
    copy the resulting session cookie, and pass it straight through.

    ``domain_headers`` is the scoped counterpart: headers applied only to
    requests whose own URL host matches a given domain (see
    `resolve_domain_headers`), so one run over a large, multi-platform
    backlink list can carry a different session cookie per login-walled
    platform without sending any of them to the unrelated majority of
    URLs in the same file.

    ``per_url_account_id``/``account_headers`` are the *account*-scoped
    counterpart to ``domain_headers`` -- for the common case where a
    single domain (e.g. facebook.com) needs several different sessions,
    one per row, not one per run. ``per_url_account_id`` maps a
    candidate's ``raw_url`` to the ``account_id`` parsed from its input
    row; ``account_headers`` is the ``--accounts-file`` registry mapping
    each ``account_id`` to its own header dict. Resolved together via
    `resolve_effective_headers`, with an account's own headers taking
    priority over a same-name header from `domain_headers` for that same
    row. A URL with no ``account_id`` is completely unaffected -- it never
    receives any account's headers, even if its host also appears in
    `account_headers` under a different row.

    Driven by the same bounded-queue-plus-sentinel worker pool shape
    ``engine.Engine.run()`` uses for `scan` -- this is a *fixed* candidate
    list (like `scan`), not a dynamically-discovered frontier (like
    `crawl`), so `Engine`'s termination strategy is the right fit, not
    `Crawler`'s unbounded queue. Request pacing goes through
    ``engine.RateLimiter``, the same shared pacer `scan`/`crawl` already
    use, so every command that fetches URLs paces identically -- `bl-check`
    has no ``--rate-limit`` flag of its own in v1, so the limiter is
    always unbounded here, but reusing the same class (rather than a
    bespoke sleep loop) keeps that door open without new code the day a
    rate limit is asked for.

    ``on_result`` is called once per URL, as soon as that URL's check
    completes (not in input order) -- the caller (``bl-check``) uses this
    to persist each result and update a progress bar incrementally, the
    same way ``on_result``/``on_page`` do for `scan`/`crawl`.
    """
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    headers = {"User-Agent": user_agent}
    if extra_headers:
        headers.update(extra_headers)
    rate_limiter = RateLimiter(None)
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=concurrency * 4)

    async def producer() -> None:
        for url in urls:
            await queue.put(url)
        for _ in range(concurrency):
            await queue.put(None)

    async def worker(client: httpx.AsyncClient) -> None:
        while True:
            url = await queue.get()
            try:
                if url is None:
                    return
                await rate_limiter.acquire()
                effective_targets = (per_url_targets or {}).get(url, target_domains)
                result = await check_one(
                    client,
                    url,
                    effective_targets,
                    allow_subdomains=allow_subdomains,
                    check_indirect=check_indirect,
                    request_headers=resolve_effective_headers(
                        url, domain_headers, per_url_account_id, account_headers
                    ),
                )
                await on_result(result)
            finally:
                queue.task_done()

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        limits=limits,
        headers=headers,
        verify=True,
    ) as client:
        producer_task = asyncio.create_task(producer())
        worker_tasks = [asyncio.create_task(worker(client)) for _ in range(concurrency)]
        await producer_task
        await asyncio.gather(*worker_tasks)


class PlaywrightNotInstalledError(RuntimeError):
    """Raised when `--browser` is used but the `js` extra isn't installed.

    Deliberately a plain `RuntimeError` subclass, not a `playwright`
    exception -- this module (and anything that imports it) must stay
    importable with zero `playwright` dependency; only *calling*
    `run_backlink_checks_browser` needs it installed.
    """


_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})


async def _block_heavy_resources(route: object) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:  # type: ignore[attr-defined]
        await route.abort()  # type: ignore[attr-defined]
    else:
        await route.continue_()  # type: ignore[attr-defined]


def _split_cookie_header(
    request_headers: Mapping[str, str] | None,
) -> tuple[str | None, dict[str, str]]:
    """Pulls the ``Cookie`` entry (matched case-insensitively, same as real
    HTTP header lookup) out of a headers dict for `check_one_browser`.

    Returns ``(cookie_value, remaining_headers)``. Everything except the
    cookie still goes through `page.set_extra_http_headers` exactly as
    before -- only the cookie itself needs the different
    `context.add_cookies()` treatment (see `check_one_browser`'s
    docstring for why).
    """
    if not request_headers:
        return None, {}
    cookie_value: str | None = None
    remaining: dict[str, str] = {}
    for name, value in request_headers.items():
        if name.strip().lower() == "cookie":
            cookie_value = value
        else:
            remaining[name] = value
    return cookie_value, remaining


def _cookie_header_to_playwright_cookies(cookie_header: str, url: str) -> list[dict[str, str]]:
    """Turns a raw ``"name=value; name2=value2"`` Cookie header value into
    individual Playwright cookie objects for `context.add_cookies()`.

    Scoped with a leading-dot domain (``.example.com``) rather than a
    bare host, so the cookie is also visible to same-site subdomains
    (e.g. a SPA on ``x.com`` whose own JS calls ``api.x.com`` after the
    page loads) -- matching how a real browser's own ``Set-Cookie``
    almost always scopes a session cookie, rather than the narrower
    host-only default `add_cookies()` would otherwise apply.
    """
    hostname = urlparse(url).hostname
    if not hostname:
        return []
    domain = hostname if hostname.startswith(".") else f".{hostname}"
    cookies: list[dict[str, str]] = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return cookies


async def check_one_browser(
    context: BrowserContext,
    url: str,
    target_domains: frozenset[str],
    *,
    allow_subdomains: bool,
    check_indirect: bool,
    nav_timeout_ms: int,
    render_wait_ms: int,
    request_headers: Mapping[str, str] | None = None,
) -> BacklinkResult:
    """Playwright-driven counterpart of `check_one`.

    Mirrors that function's check ordering exactly (bot-block status ->
    login wall -> challenge page -> final_url_is_target -> anchor scan ->
    indirect fallback -> text-mention fallback), including the same
    `robots_meta`/`robots_header` capture, so results from both modes are
    directly comparable row for row -- ported from what was
    `backlink_checker_js.py`'s `check_one`.

    Any non-``Cookie`` entry in ``request_headers`` is set via
    `page.set_extra_http_headers` -- scoped to this one page/URL, not the
    shared `context` -- so a domain-scoped header (see
    `resolve_domain_headers`) never leaks onto the other concurrent tabs
    checking unrelated hosts.

    A ``Cookie`` entry gets different treatment: `context.add_cookies()`
    instead. `set_extra_http_headers` only appends a raw ``Cookie:`` line
    to the outgoing network request -- it never populates the browser's
    actual cookie jar, so a page whose own JavaScript re-reads
    `document.cookie` to decide whether it's logged in (any client-
    rendered SPA that keeps auth state there instead of only ever
    looking at the incoming request header -- e.g. X/Twitter) sees no
    cookie at all and behaves as logged-out, silently defeating the
    whole point of pasting a session cookie in for that page. That's
    also what produces the "page never finishes loading" symptom
    (`domcontentloaded` timing out) this was added to fix, on top of the
    login-wall/blocked-content result a purely header-based cookie would
    already have produced. Because `add_cookies()` writes to the
    *shared* `context`'s cookie jar rather than being page-scoped like
    the header call, the cookie is added right before navigation and
    removed again in the `finally` block below by exact name/domain/path
    -- preserving the same "never leaks onto other concurrent tabs"
    guarantee the header-only approach gave for free.
    """
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    result = BacklinkResult(source_url=url)
    page: Page = await context.new_page()
    cookie_value, other_headers = _split_cookie_header(request_headers)
    added_cookies = _cookie_header_to_playwright_cookies(cookie_value, url) if cookie_value else []
    try:
        if other_headers:
            await page.set_extra_http_headers(other_headers)
        if added_cookies:
            await context.add_cookies(added_cookies)  # type: ignore[arg-type]
        try:
            response = await page.goto(url, timeout=nav_timeout_ms, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            result.error = f"TimeoutError: navigation exceeded {nav_timeout_ms}ms"
            return result
        except Exception as exc:  # noqa: BLE001 - surfaced per-URL, must not kill the whole run
            result.error = f"{type(exc).__name__}: {exc}"
            return result

        # This is the entire reason browser mode exists: give the SPA's own
        # JS time to fetch data and hydrate the DOM before we look at it.
        # Best-effort -- some pages (ads/analytics polling) never truly go
        # idle, so a timeout here is not a failure, just "rendered enough".
        with contextlib.suppress(PlaywrightTimeoutError):
            await page.wait_for_load_state("networkidle", timeout=render_wait_ms)

        result.final_url = page.url
        result.status_code = response.status if response is not None else None
        headers = response.headers if response is not None else {}
        result.robots_header = headers.get("x-robots-tag")

        if result.status_code is not None and looks_like_bot_block_status(result.status_code):
            result.blocked = True
            result.notes.append(
                f"HTTP {result.status_code} — anti-scraping block (e.g. LinkedIn), not a real "
                "'not found'; the server refused to serve any content to an automated request"
            )
            return result

        if result.status_code is not None and result.status_code >= 400:
            result.notes.append(f"HTTP {result.status_code}")

        if looks_like_login_wall(result.final_url):
            result.requires_login = True
            result.notes.append(
                "redirected to a login page instead of the real content — page requires "
                "authentication to view, so no real conclusion about the backlink can be drawn"
            )
            return result

        html = await page.content()
        tree = HTMLParser(html)
        result.robots_meta = _extract_robots_meta(tree)
        indirect_pattern = build_text_mention_pattern_any(target_domains)

        title_node = tree.css_first("title")
        title = title_node.text(strip=True) if title_node else None
        if result.status_code is not None and looks_like_challenge_page(result.status_code, title, headers):
            result.blocked = True
            result.notes.append("looks like a bot-challenge / interstitial page, not the real content")
            return result

        final_hostname = urlparse(result.final_url).hostname
        final_match = (
            hostname_matches_any(final_hostname, target_domains, allow_subdomains=allow_subdomains)
            if final_hostname
            else None
        )
        if final_match is not None:
            result.match_found = True
            result.match_type = "final_url_is_target"
            result.matched_target = final_match
            result.notes.append(
                "the request landed directly on a target domain (e.g. a short-link redirect) — "
                "strongest possible signal, independent of any <a href> on the page"
            )
            return result

        # --- Primary pass: real <a href> hostname match, on the *rendered* DOM
        indirect_candidate: tuple[str, str | None, str | None, str] | None = None
        for anchor in tree.css("a"):
            href = anchor.attributes.get("href")
            if not href:
                continue
            href = href.strip()
            if not href or href.lower().startswith(_NON_NAVIGABLE_PREFIXES):
                continue

            hostname = extract_hostname(href, result.final_url)
            anchor_match = (
                hostname_matches_any(hostname, target_domains, allow_subdomains=allow_subdomains)
                if hostname
                else None
            )
            if anchor_match is not None:
                is_exact = normalize_hostname(hostname) == normalize_domain(anchor_match)  # type: ignore[arg-type]
                result.match_found = True
                result.match_type = "anchor" if is_exact else "subdomain_anchor"
                result.matched_href = href
                result.matched_target = anchor_match
                result.rel = anchor.attributes.get("rel")
                result.target = anchor.attributes.get("target")
                return result

            if check_indirect and indirect_candidate is None:
                indirect_match = indirect_pattern.search(href)
                if indirect_match:
                    matched_text = indirect_match.group(0).lower()
                    matched_domain = next(
                        (t for t in target_domains if normalize_domain(t) == matched_text), matched_text
                    )
                    indirect_candidate = (
                        href,
                        anchor.attributes.get("rel"),
                        anchor.attributes.get("target"),
                        matched_domain,
                    )

        if indirect_candidate is not None:
            result.match_found = True
            result.match_type = "indirect_query"
            result.matched_href = indirect_candidate[0]
            result.rel = indirect_candidate[1]
            result.target = indirect_candidate[2]
            result.matched_target = indirect_candidate[3]
            result.notes.append(
                "domain only found embedded inside another host's URL (e.g. a redirector/tracker) — "
                "verify manually, this is a weaker signal than a direct anchor"
            )
            return result

        # --- Fallback: plain-text mention, not inside any href -------------
        body_text = tree.body.text(separator=" ", strip=True) if tree.body else ""
        mentions = indirect_pattern.findall(body_text)
        if mentions:
            result.text_mentions = len(mentions)
            result.match_type = "text_mention_only"
            result.notes.append(
                "domain appears as plain rendered text but not inside any <a href> — "
                "even after JS rendering this is not a real backlink"
            )

        return result
    finally:
        if added_cookies:
            # Scrub exactly the cookies this check added -- by name +
            # domain + path, not a blanket `clear_cookies()` -- so a
            # concurrent tab's own cookies (a different account, or a
            # different domain entirely) are never touched.
            for cookie in added_cookies:
                await context.clear_cookies(
                    name=cookie["name"], domain=cookie["domain"], path=cookie["path"]
                )
        await page.close()


async def run_backlink_checks_browser(
    urls: Sequence[str],
    target_domains: frozenset[str],
    *,
    concurrency: int,
    nav_timeout: float,
    render_wait: float,
    allow_subdomains: bool,
    check_indirect: bool,
    user_agent: str,
    headed: bool,
    block_resources: bool,
    extra_headers: Mapping[str, str] | None = None,
    domain_headers: Mapping[str, Mapping[str, str]] | None = None,
    per_url_targets: Mapping[str, frozenset[str]] | None = None,
    per_url_account_id: Mapping[str, str] | None = None,
    account_headers: Mapping[str, Mapping[str, str]] | None = None,
    on_result: Callable[[BacklinkResult], Awaitable[None]],
) -> None:
    """Browser-mode counterpart of `run_backlink_checks` (Playwright, not httpx).

    Same `on_result`-per-completion streaming contract as
    `run_backlink_checks` -- the caller (`bl-check --browser`) persists
    each result and updates the progress bar the same way regardless of
    which mode is running. `concurrency` here means concurrent browser
    *tabs*, not HTTP connections -- keep this much lower than the httpx
    mode's default; each one is a real page load, not a lightweight
    request (`bl-check` picks a lower default automatically when
    `--browser` is set and `-c` isn't given explicitly -- see `cli.py`).

    ``extra_headers`` is set on the `BrowserContext` (so it rides along
    on every navigation, including any redirects the page itself makes)
    -- the same manual "paste a session cookie" model as httpx mode. This
    still isn't a login flow: Playwright is only used to *render* the
    page, never to fill in and submit a login form.

    ``domain_headers``/``per_url_account_id``+``account_headers``, unlike
    ``extra_headers``, are deliberately *not* set on the shared
    `BrowserContext` -- they're resolved per-URL (`resolve_effective_headers`)
    and applied via `page.set_extra_http_headers` inside `check_one_browser`,
    scoped to that one page/tab. This is what keeps one row's
    account-specific session from leaking onto the other concurrent tabs
    checking different rows (different accounts, or no account at all) in
    the same run.

    Raises `PlaywrightNotInstalledError` if the `js` extra
    (`pip install redirecthunter[js]`) isn't installed -- checked here,
    not at module import time, so `redirecthunter/backlink.py` (and
    everything that imports it) stays importable without `playwright`.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise PlaywrightNotInstalledError(
            "Browser mode needs Playwright, which isn't installed. Run:\n"
            "  pip install redirecthunter[js]\n"
            "  playwright install chromium"
        ) from exc

    nav_timeout_ms = int(nav_timeout * 1000)
    render_wait_ms = int(render_wait * 1000)
    sem = asyncio.Semaphore(concurrency)

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=not headed)
        context: BrowserContext = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1280, "height": 900},
            extra_http_headers=dict(extra_headers) if extra_headers else None,
        )
        if block_resources:
            await context.route("**/*", _block_heavy_resources)

        try:

            async def _bounded(u: str) -> None:
                async with sem:
                    effective_targets = (per_url_targets or {}).get(u, target_domains)
                    result = await check_one_browser(
                        context,
                        u,
                        effective_targets,
                        allow_subdomains=allow_subdomains,
                        check_indirect=check_indirect,
                        nav_timeout_ms=nav_timeout_ms,
                        render_wait_ms=render_wait_ms,
                        request_headers=resolve_effective_headers(
                            u, domain_headers, per_url_account_id, account_headers
                        ),
                    )
                    await on_result(result)

            await asyncio.gather(*(_bounded(u) for u in urls))
        finally:
            await context.close()
            await browser.close()
