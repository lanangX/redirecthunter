"""Async site crawler: link-graph discovery, broken-link, and on-page SEO auditing.

This is the "crawl like Ahrefs Site Audit" counterpart to
:mod:`redirecthunter.engine`. Where ``Engine`` validates redirects for a
*fixed* list of candidate URLs, :class:`Crawler` starts from one or more
seeds and discovers its own work list by following links it finds on each
page it fetches -- the total amount of work is not known up front.

That single difference (dynamic vs. fixed work list) is why this is a
separate module rather than a mode flag on ``Engine``: the worker-pool
termination strategy has to change (``asyncio.Queue.join()`` instead of
sentinel values -- see :meth:`Crawler.run`), and every fetched page does
two jobs ``Engine`` never has to (extract on-page SEO signals, discover
more frontier items) instead of one (validate a redirect chain).

Everything HTTP-transport-shaped (retry/backoff, the shared connection
pool, ``MAX_BODY_BYTES`` body capping) is intentionally reused from
:mod:`redirecthunter.engine` rather than re-implemented, since none of
that logic is specific to "fixed list" vs. "discovered frontier" -- only
the request-dispatch loop around it differs.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urldefrag, urlsplit, urlunsplit

import httpx
from selectolax.parser import HTMLParser

from redirecthunter.engine import MAX_BODY_BYTES, RateLimiter
from redirecthunter.models import (
    CrawlConfig,
    CrawlLinkResult,
    CrawlPageResult,
    LinkKind,
    PageIssue,
)
from redirecthunter.utils import (
    extract_domain,
    is_external_domain,
    is_valid_http_url,
    normalize_url,
    resolve_relative_url,
)

#: Ceiling applied to computed retry backoff delays. Mirrors
#: ``engine._MAX_BACKOFF_SECONDS`` -- kept as a separate constant (not
#: imported) since it's a private module-level default, not part of
#: engine.py's public contract.
_MAX_BACKOFF_SECONDS = 10.0

#: Anchor href prefixes that are never worth resolving as a link: they
#: either run client-side code, open a non-HTTP protocol handler, or
#: (bare "#") point back at the same document.
_SKIP_HREF_PREFIXES = ("javascript:", "mailto:", "tel:", "sms:", "#")

PageCallback = Callable[[CrawlPageResult], Awaitable[None]]
LinkCallback = Callable[[CrawlLinkResult], Awaitable[None]]


class CrawlerError(Exception):
    """Raised on unrecoverable crawler setup errors (e.g. an invalid seed URL)."""


class CrawlRequestError(Exception):
    """Raised when a request fails after exhausting all configured retries.

    Deliberately a distinct type from ``engine.EngineRequestError`` even
    though the two are structurally identical -- each async HTTP loop in
    this codebase owns its own failure type rather than the crawler
    reaching into the engine's exception hierarchy for something that
    isn't actually shared behavior, only a shared shape.
    """


@dataclass(slots=True)
class CrawlStats:
    """Live-updating counters for the currently running (or just-finished) crawl.

    Polled from a separate coroutine (e.g. a Rich progress display) while
    :meth:`Crawler.run` is still in flight, same pattern as
    ``engine.EngineStats``.
    """

    pages_reserved: int = 0
    pages_completed: int = 0
    pages_alive: int = 0
    pages_dead: int = 0
    links_checked: int = 0
    links_broken: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def pages_per_second(self) -> float:
        elapsed = self.elapsed_seconds
        return self.pages_completed / elapsed if elapsed > 0 else 0.0


@dataclass(slots=True)
class _FrontierItem:
    """One unit of work on the crawl queue -- either a page to fetch/parse, or a link to status-check."""

    kind: Literal["page", "link_check"]
    url: str
    depth: int = 0
    discovered_from: str | None = None
    raw_href: str = ""
    anchor_text: str | None = None
    rel: str | None = None
    target_attr: str | None = None
    link_kind: LinkKind = LinkKind.INTERNAL


def _strip_fragment(url: str) -> str:
    stripped, _fragment = urldefrag(url)
    return stripped


def _dedupe_key(url: str, *, include_query: bool) -> str:
    """Build the key used to dedupe pages and link-check targets for one crawl.

    Always fragment-insensitive (``#section`` never changes what the
    server returns). Query-insensitive too when
    ``CrawlConfig.include_query_string`` is False, so e.g.
    ``/products?sort=price`` and ``/products?sort=name`` collapse to one
    frontier entry instead of each being crawled as a "different" page.
    """
    stripped = _strip_fragment(url)
    if include_query:
        return stripped
    parts = urlsplit(stripped)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _decode_body(raw: bytes) -> str | None:
    """Decode a raw response body to text for HTML parsing. See ``engine._decode_body``."""
    if not raw:
        return None
    return raw.decode("utf-8", errors="replace")


def _looks_like_html(content_type: str | None, body_text: str | None) -> bool:
    """Best-effort decision on whether a response body is worth parsing as HTML.

    Trusts an explicit ``Content-Type: text/html`` header when present.
    When the header is missing or generic (some misconfigured servers omit
    it, or send ``application/octet-stream`` for everything), falls back
    to sniffing for a ``<html`` opening tag near the start of the body
    rather than refusing to parse -- a mislabeled HTML page is common
    enough in the wild that skipping it outright would silently blind the
    crawler to a real page's links.
    """
    if content_type and "html" in content_type.lower():
        return True
    if content_type and content_type.split(";")[0].strip().lower() not in ("", "application/octet-stream"):
        return False
    if not body_text:
        return False
    return "<html" in body_text[:1024].lower()


class Crawler:
    """Async BFS site crawler: worker pool + HTTP client + link-graph discovery.

    Constructed with a resolved :class:`~redirecthunter.models.CrawlConfig`
    and the (already resolved) set of seed URLs to start from -- resolving
    *which* URLs are seeds (a single domain seed, or every row of an input
    file) is :mod:`redirecthunter.cli`'s job, same division of
    responsibility as ``Engine`` never reading raw CLI args or input files
    itself.
    """

    def __init__(self, config: CrawlConfig) -> None:
        self._config = config
        self._rate_limiter = RateLimiter(config.rate_limit)
        self.stats = CrawlStats()
        # Every hostname treated as "internal" scope for this crawl: every
        # seed's own host, plus anything in allowed_domains. Populated by
        # run() from the actual seed URLs, since CrawlConfig.seed_url alone
        # doesn't cover CrawlSeedMode.URL_LIST (many seeds, possibly many
        # hosts).
        self._reference_domains: set[str] = {d.lower().lstrip(".") for d in config.allowed_domains}
        self._visited_pages: set[str] = set()
        self._reserved_page_slots = 0
        self._link_status_cache: dict[str, CrawlLinkResult] = {}
        self._lock = asyncio.Lock()

    def _build_client(self) -> httpx.AsyncClient:
        """Construct the single shared, connection-pooled HTTP client for this crawl.

        ``follow_redirects=True`` here (unlike ``Engine``, which drives its
        own hop loop): a crawl only needs to know a page's *final*
        destination to fetch/parse it and to classify a link as broken or
        not -- it has no use for RedirectHunter's redirect-type
        classification (that's what ``scan`` is for). ``response.history``
        still exposes whether a redirect happened, which is all a crawl
        report needs.
        """
        limits = httpx.Limits(
            max_connections=self._config.workers * 2,
            max_keepalive_connections=self._config.workers,
        )
        timeout = httpx.Timeout(self._config.timeout, connect=self._config.connect_timeout)
        headers = {"User-Agent": self._config.user_agent, **self._config.extra_headers}
        return httpx.AsyncClient(
            http2=self._config.http2,
            limits=limits,
            timeout=timeout,
            follow_redirects=True,
            max_redirects=10,
            verify=self._config.verify_tls,
            proxy=self._config.proxy,
            headers=headers,
        )

    async def _send(
        self, client: httpx.AsyncClient, method: str, url: str, *, read_body: bool
    ) -> tuple[httpx.Response, bytes]:
        """Send a single HTTP request, capping and optionally skipping the body.

        ``read_body=False`` (used for link-status checks) closes the
        connection as soon as headers/status/final-URL are known, without
        ever reading the response body -- checking a link's health should
        never mean downloading a multi-MB file at the other end of it.
        """
        request = client.build_request(method, url)
        response = await client.send(request, stream=True)
        if not read_body:
            await response.aclose()
            return response, b""

        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_BODY_BYTES:
                    break
        finally:
            await response.aclose()
        return response, b"".join(chunks)

    async def _send_with_retry(
        self, client: httpx.AsyncClient, method: str, url: str, *, read_body: bool
    ) -> tuple[httpx.Response, bytes]:
        """Send a request, retrying transport-level failures with exponential backoff.

        Only ``httpx.RequestError`` (connection refused, DNS failure,
        timeout, TLS failure) triggers a retry -- HTTP error status codes
        (4xx/5xx) are a valid, meaningful response (often exactly what a
        broken-link check is looking for), never retried here.
        """
        attempts = self._config.retry + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._send(client, method, url, read_body=read_body)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    delay = min(self._config.retry_backoff * (2**attempt), _MAX_BACKOFF_SECONDS)
                    await asyncio.sleep(delay)
        raise CrawlRequestError(f"{type(last_exc).__name__}: {last_exc}") from last_exc

    def _classify(self, url: str) -> LinkKind:
        """Classify a URL as internal/external scope for this crawl.

        Hostname-boundary comparison via
        :func:`~redirecthunter.utils.is_external_domain` against every
        known reference domain -- internal if it matches *any* of them,
        external only if it matches none. A URL is internal as soon as one
        reference domain claims it, so a multi-domain crawl (several seeds
        from ``CrawlSeedMode.URL_LIST``, or ``allowed_domains``) doesn't
        misclassify a link between two of its own seed domains as external.
        """
        for domain in self._reference_domains:
            if not is_external_domain(url, domain):
                return LinkKind.INTERNAL
        return LinkKind.EXTERNAL

    def _extract_page_signals(
        self, page_url: str, tree: HTMLParser
    ) -> tuple[str | None, str | None, list[str], int, list[tuple[str, str, str | None, str | None, str | None]]]:
        """Parse one page's DOM for title/meta description/H1s/word count/links.

        Returns:
            ``(title, meta_description, h1_texts, word_count, links)`` where
            ``links`` is a list of ``(raw_href, resolved_absolute_url,
            anchor_text, rel, target_attr)`` tuples, deduplicated by resolved
            URL (a page linking the same target ten times counts as one link
            for SEO-audit purposes, though every crawl-wide occurrence is
            still requested at most once thanks to the crawler's link-status
            cache regardless of this per-page dedup). ``rel``/``target_attr``
            are the anchor's raw, unparsed ``rel``/``target`` attribute
            values (e.g. ``"nofollow"``, ``"_blank"``), taken from whichever
            occurrence of the resolved URL is kept by the dedup.
        """
        title: str | None = None
        title_node = tree.css_first("title")
        if title_node is not None:
            text = title_node.text(strip=True)
            title = text or None

        meta_description: str | None = None
        for meta in tree.css("meta"):
            name_attr = (meta.attributes.get("name") or "").strip().lower()
            if name_attr != "description":
                continue
            content = meta.attributes.get("content")
            if content and content.strip():
                meta_description = content.strip()
            break

        h1_texts = [text for node in tree.css("h1") if (text := node.text(strip=True))]

        word_count = 0
        body_node = tree.css_first("body")
        if body_node is not None:
            word_count = len(body_node.text(separator=" ", strip=True).split())

        seen_targets: set[str] = set()
        links: list[tuple[str, str, str | None, str | None, str | None]] = []
        for anchor in tree.css("a"):
            href = anchor.attributes.get("href")
            if not href:
                continue
            href = href.strip()
            if not href or href.lower().startswith(_SKIP_HREF_PREFIXES):
                continue

            resolved = _strip_fragment(resolve_relative_url(page_url, href))
            if not is_valid_http_url(resolved):
                continue
            resolved = normalize_url(resolved)

            if resolved in seen_targets:
                continue
            seen_targets.add(resolved)

            anchor_text = anchor.text(strip=True) or None
            rel = anchor.attributes.get("rel")
            target_attr = anchor.attributes.get("target")
            links.append((href, resolved, anchor_text, rel, target_attr))

        return title, meta_description, h1_texts, word_count, links

    def _compute_issues(self, title: str | None, meta_description: str | None, h1_texts: list[str]) -> list[PageIssue]:
        """Derive on-page SEO issue flags this page's own response can prove by itself."""
        issues: list[PageIssue] = []
        cfg = self._config

        if not title:
            issues.append(PageIssue.MISSING_TITLE)
        elif len(title) < cfg.title_min_length:
            issues.append(PageIssue.TITLE_TOO_SHORT)
        elif len(title) > cfg.title_max_length:
            issues.append(PageIssue.TITLE_TOO_LONG)

        if not meta_description:
            issues.append(PageIssue.MISSING_META_DESCRIPTION)
        elif len(meta_description) > cfg.meta_description_max_length:
            issues.append(PageIssue.META_DESCRIPTION_TOO_LONG)

        if not h1_texts:
            issues.append(PageIssue.MISSING_H1)
        elif len(h1_texts) > 1:
            issues.append(PageIssue.MULTIPLE_H1)

        return issues

    async def _reserve_page_slot(self) -> bool:
        """Atomically claim one of ``max_pages`` page slots, if any remain."""
        async with self._lock:
            if self._reserved_page_slots >= self._config.max_pages:
                return False
            self._reserved_page_slots += 1
            self.stats.pages_reserved = self._reserved_page_slots
            return True

    async def _mark_visited(self, key: str) -> bool:
        """Return True and mark ``key`` visited if it hasn't been claimed by another item yet."""
        async with self._lock:
            if key in self._visited_pages:
                return False
            self._visited_pages.add(key)
            return True

    async def _enqueue_seed(
        self, queue: asyncio.Queue[_FrontierItem], url: str
    ) -> None:
        key = _dedupe_key(url, include_query=self._config.include_query_string)
        if not await self._mark_visited(key):
            return
        if not await self._reserve_page_slot():
            return
        await queue.put(_FrontierItem(kind="page", url=url, depth=0, discovered_from=None))

    async def _enqueue_discovered_link(
        self,
        queue: asyncio.Queue[_FrontierItem],
        *,
        source_url: str,
        raw_href: str,
        target_url: str,
        anchor_text: str | None,
        rel: str | None,
        target_attr: str | None,
        depth: int,
    ) -> None:
        """Decide whether a link found on a page becomes a new page to crawl, a link to status-check, or neither."""
        link_kind = self._classify(target_url)
        cfg = self._config

        if link_kind is LinkKind.EXTERNAL:
            if not cfg.check_external_links:
                return
            await self._enqueue_link_check(
                queue,
                url=target_url,
                source_url=source_url,
                raw_href=raw_href,
                anchor_text=anchor_text,
                rel=rel,
                target_attr=target_attr,
                link_kind=link_kind,
            )
            return

        # Internal link: try to crawl it as a full page if it's in scope and
        # we haven't already committed to visiting it; otherwise fall back
        # to a lighter status-only check so an out-of-budget/out-of-depth
        # link is still validated, just never itself expanded further.
        in_depth = depth <= cfg.max_depth
        key = _dedupe_key(target_url, include_query=cfg.include_query_string)

        if in_depth and cfg.follow_links:
            newly_visited = await self._mark_visited(key)
            if newly_visited:
                if await self._reserve_page_slot():
                    await queue.put(
                        _FrontierItem(
                            kind="page",
                            url=target_url,
                            depth=depth,
                            discovered_from=source_url,
                        )
                    )
                    return
                # Page budget exhausted -- undo the visited-claim so a link
                # check (below) isn't skipped as "already handled".
                async with self._lock:
                    self._visited_pages.discard(key)

        await self._enqueue_link_check(
            queue,
            url=target_url,
            source_url=source_url,
            raw_href=raw_href,
            anchor_text=anchor_text,
            rel=rel,
            target_attr=target_attr,
            link_kind=link_kind,
        )

    async def _enqueue_link_check(
        self,
        queue: asyncio.Queue[_FrontierItem],
        *,
        url: str,
        source_url: str,
        raw_href: str,
        anchor_text: str | None,
        rel: str | None,
        target_attr: str | None,
        link_kind: LinkKind,
    ) -> None:
        await queue.put(
            _FrontierItem(
                kind="link_check",
                url=url,
                discovered_from=source_url,
                raw_href=raw_href,
                anchor_text=anchor_text,
                rel=rel,
                target_attr=target_attr,
                link_kind=link_kind,
            )
        )

    async def _process_page(
        self,
        client: httpx.AsyncClient,
        item: _FrontierItem,
        queue: asyncio.Queue[_FrontierItem],
        on_page: PageCallback,
    ) -> None:
        """Fetch, parse, and persist one page, then enqueue whatever it links to."""
        start = time.monotonic()
        await self._rate_limiter.acquire()

        try:
            response, raw_body = await self._send_with_retry(client, "GET", item.url, read_body=True)
        except CrawlRequestError as exc:
            page = CrawlPageResult(
                crawl_id=self._config.crawl_id,
                url=item.url,
                depth=item.depth,
                discovered_from=item.discovered_from,
                alive=False,
                error=str(exc),
                issues=[PageIssue.MISSING_TITLE, PageIssue.MISSING_META_DESCRIPTION, PageIssue.MISSING_H1],
                latency_ms=(time.monotonic() - start) * 1000,
            )
            async with self._lock:
                self.stats.pages_completed += 1
                self.stats.pages_dead += 1
            await on_page(page)
            return

        latency_ms = (time.monotonic() - start) * 1000
        body_text = _decode_body(raw_body)
        content_type = response.headers.get("content-type")

        title = meta_description = None
        h1_texts: list[str] = []
        word_count = 0
        links: list[tuple[str, str, str | None, str | None, str | None]] = []

        if body_text and _looks_like_html(content_type, body_text):
            tree = HTMLParser(body_text)
            title, meta_description, h1_texts, word_count, links = self._extract_page_signals(
                str(response.url), tree
            )

        internal_targets = [t for t in links if self._classify(t[1]) is LinkKind.INTERNAL]
        external_targets = [t for t in links if self._classify(t[1]) is LinkKind.EXTERNAL]

        page = CrawlPageResult(
            crawl_id=self._config.crawl_id,
            url=item.url,
            depth=item.depth,
            discovered_from=item.discovered_from,
            status_code=response.status_code,
            alive=True,
            redirected=len(response.history) > 0,
            final_url=str(response.url) if response.history else None,
            content_type=content_type,
            title=title,
            title_length=len(title) if title else 0,
            meta_description=meta_description,
            meta_description_length=len(meta_description) if meta_description else 0,
            h1_texts=h1_texts,
            h1_count=len(h1_texts),
            internal_link_count=len(internal_targets),
            external_link_count=len(external_targets),
            word_count=word_count,
            issues=self._compute_issues(title, meta_description, h1_texts),
            latency_ms=latency_ms,
        )

        async with self._lock:
            self.stats.pages_completed += 1
            self.stats.pages_alive += 1

        await on_page(page)

        if not self._config.follow_links:
            # Seed-list, no-crawl mode: still validate what's *on* the page
            # (both internal and external), just never expand internal
            # links into new pages of their own.
            for raw_href, target_url, anchor_text, rel, target_attr in links:
                link_kind = self._classify(target_url)
                if link_kind is LinkKind.EXTERNAL and not self._config.check_external_links:
                    continue
                await self._enqueue_link_check(
                    queue,
                    url=target_url,
                    source_url=item.url,
                    raw_href=raw_href,
                    anchor_text=anchor_text,
                    rel=rel,
                    target_attr=target_attr,
                    link_kind=link_kind,
                )
            return

        for raw_href, target_url, anchor_text, rel, target_attr in links:
            await self._enqueue_discovered_link(
                queue,
                source_url=item.url,
                raw_href=raw_href,
                target_url=target_url,
                anchor_text=anchor_text,
                rel=rel,
                target_attr=target_attr,
                depth=item.depth + 1,
            )

    async def _process_link_check(
        self, client: httpx.AsyncClient, item: _FrontierItem, on_link: LinkCallback
    ) -> None:
        """Status-check one discovered link, reusing a cached result if this exact URL was already checked."""
        cache_key = _dedupe_key(item.url, include_query=self._config.include_query_string)

        async with self._lock:
            cached = self._link_status_cache.get(cache_key)
            if cached is not None:
                self.stats.links_checked += 1
                if cached.is_broken:
                    self.stats.links_broken += 1
        if cached is not None:
            result = cached.model_copy(
                update={
                    "link_id": str(uuid.uuid4()),
                    "source_page_url": item.discovered_from or "",
                    "raw_href": item.raw_href,
                    "anchor_text": item.anchor_text,
                    "rel": item.rel,
                    "target_attr": item.target_attr,
                }
            )
            await on_link(result)
            return

        start = time.monotonic()
        await self._rate_limiter.acquire()

        status_code: int | None = None
        final_url: str | None = None
        redirected = False
        error: str | None = None
        is_broken = True

        try:
            response, _body = await self._send_with_retry(client, "HEAD", item.url, read_body=False)
            if response.status_code in (405, 501):
                # Some servers reject HEAD outright -- fall back to a
                # body-discarding GET rather than reporting a false positive.
                response, _body = await self._send_with_retry(client, "GET", item.url, read_body=False)
            status_code = response.status_code
            redirected = len(response.history) > 0
            final_url = str(response.url) if redirected else None
            is_broken = response.status_code >= 400
        except CrawlRequestError as exc:
            error = str(exc)

        latency_ms = (time.monotonic() - start) * 1000

        result = CrawlLinkResult(
            crawl_id=self._config.crawl_id,
            source_page_url=item.discovered_from or "",
            target_url=item.url,
            raw_href=item.raw_href,
            link_kind=item.link_kind,
            anchor_text=item.anchor_text,
            rel=item.rel,
            target_attr=item.target_attr,
            status_code=status_code,
            is_broken=is_broken,
            redirected=redirected,
            final_url=final_url,
            error=error,
            latency_ms=latency_ms,
        )

        async with self._lock:
            self._link_status_cache[cache_key] = result
            self.stats.links_checked += 1
            if is_broken:
                self.stats.links_broken += 1

        await on_link(result)

    async def run(
        self,
        seeds: Iterable[str],
        on_page: PageCallback,
        on_link: LinkCallback,
    ) -> CrawlStats:
        """Crawl every seed URL, discovering and following (or just checking) links as it goes.

        Uses a producer/consumer pattern over a single **unbounded**
        ``asyncio.Queue`` shared by every worker: workers both consume
        from it and produce onto it (a fetched page enqueues its own
        outlinks before finishing), so termination can't be driven by a
        fixed-size producer + sentinel values the way ``Engine.run`` does
        it. Instead every ``put`` happens *before* the corresponding
        ``task_done()`` of the item that discovered it, so
        ``asyncio.Queue.join()`` only returns once the entire frontier --
        including everything discovered along the way -- has actually
        drained.

        The queue is intentionally unbounded (unlike ``Engine``'s
        ``workers * 4``-bounded one): a crawl's frontier size is bounded by
        ``max_pages`` (page slots are reserved before a page is ever
        queued -- see ``_reserve_page_slot``) and by the number of links a
        single page can realistically contain, not by an arbitrary queue
        depth: a bounded queue here would risk a deadlock if every worker
        is blocked trying to ``put`` more discovered links than the queue
        can briefly hold.

        Args:
            seeds: One or more starting URLs. Materialized into a list up
                front (unlike ``Engine``'s lazily-streamed candidates) --
                a crawl needs the complete seed set before dispatching
                anything, to compute ``_reference_domains`` (internal vs.
                external scope) once, correctly, for every seed.
            on_page: Async callback invoked once per fetched page.
            on_link: Async callback invoked once per checked link
                occurrence (one per source-page/target-URL pair, even
                when the underlying HTTP check itself was deduplicated).

        Returns:
            The final :class:`CrawlStats` for this run.

        Raises:
            CrawlerError: If no seed resolves to a valid, parseable URL.
        """
        self.stats = CrawlStats()
        queue: asyncio.Queue[_FrontierItem] = asyncio.Queue()
        seed_list = list(seeds)

        for seed in seed_list:
            self._reference_domains.update(self._seed_domains(seed))

        if not self._reference_domains:
            raise CrawlerError("No valid seed URL(s) supplied -- nothing to crawl.")

        for seed in seed_list:
            await self._enqueue_seed(queue, seed)

        async def worker(client: httpx.AsyncClient) -> None:
            while True:
                item = await queue.get()
                try:
                    if item.kind == "page":
                        await self._process_page(client, item, queue, on_page)
                    else:
                        await self._process_link_check(client, item, on_link)
                except Exception as exc:  # noqa: BLE001 - one bad item must never abort the crawl
                    # Mirrors Engine._process_one's catch-all: even a truly
                    # unexpected failure (a bug, not just a network error --
                    # those are already handled inside _process_page /
                    # _process_link_check) still produces a persisted record
                    # rather than silently vanishing from the crawl's output.
                    async with self._lock:
                        if item.kind == "page":
                            self.stats.pages_completed += 1
                            self.stats.pages_dead += 1
                        else:
                            self.stats.links_checked += 1
                            self.stats.links_broken += 1
                    if item.kind == "page":
                        await on_page(
                            CrawlPageResult(
                                crawl_id=self._config.crawl_id,
                                url=item.url,
                                depth=item.depth,
                                discovered_from=item.discovered_from,
                                alive=False,
                                error=f"Unexpected error: {type(exc).__name__}: {exc}",
                                issues=[
                                    PageIssue.MISSING_TITLE,
                                    PageIssue.MISSING_META_DESCRIPTION,
                                    PageIssue.MISSING_H1,
                                ],
                            )
                        )
                    else:
                        await on_link(
                            CrawlLinkResult(
                                crawl_id=self._config.crawl_id,
                                source_page_url=item.discovered_from or "",
                                target_url=item.url,
                                raw_href=item.raw_href,
                                link_kind=item.link_kind,
                                anchor_text=item.anchor_text,
                                rel=item.rel,
                                target_attr=item.target_attr,
                                is_broken=True,
                                error=f"Unexpected error: {type(exc).__name__}: {exc}",
                            )
                        )
                finally:
                    queue.task_done()

        async with self._build_client() as client:
            worker_tasks = [asyncio.create_task(worker(client)) for _ in range(self._config.workers)]
            try:
                await queue.join()
            finally:
                for task in worker_tasks:
                    task.cancel()
                await asyncio.gather(*worker_tasks, return_exceptions=True)

        return self.stats

    @staticmethod
    def _seed_domains(seed_url: str) -> set[str]:
        domain = extract_domain(seed_url)
        return {domain.lower()} if domain else set()


__all__ = [
    "Crawler",
    "CrawlerError",
    "CrawlRequestError",
    "CrawlStats",
    "PageCallback",
    "LinkCallback",
]
