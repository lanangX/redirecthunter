"""Async HTTP engine: the networking and concurrency core of RedirectHunter.

Owns everything the "HTTP ENGINE" section of the project specification
calls for — a shared, connection-pooled ``httpx.AsyncClient`` (HTTP/2,
proxy, TLS verification, custom headers), retry-with-backoff, a bounded-rate
limiter shared across the whole worker pool, and a producer/consumer worker
pool that keeps memory bounded no matter how many candidate URLs are being
scanned (100k+).

This module does **not** know about SQLite, Rich progress bars, or the
CLI — it depends only on :mod:`redirecthunter.analyzer` (for turning
responses into results) and :mod:`redirecthunter.models`/``utils``. Results
are delivered to the caller via an injected ``on_result`` async callback,
so ``cli.py`` decides what to do with each result (persist it, update a
progress bar) without the engine needing to know either concern exists —
a direct application of the Dependency Inversion Principle.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field

import httpx

from redirecthunter.analyzer import HopAnalysis, ResponseAnalyzer
from redirecthunter.models import (
    CandidateURL,
    RedirectHop,
    RedirectResult,
    RedirectType,
    ScanConfig,
)
from redirecthunter.utils import MissingTargetError, expand_target

#: Cap on GET response bodies read from the network, in bytes. Redirect
#: detection (meta-refresh, inline JS) only ever needs content from the
#: `<head>` of a page; reading a full multi-hundred-MB body would be pure
#: waste at 100k-URL scale, both in memory and in wall-clock time. The
#: connection is closed as soon as this cap is reached rather than waiting
#: for the full body to arrive.
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB

#: Ceiling applied to computed retry backoff delays, regardless of
#: ``ScanConfig.retry_backoff`` and attempt count, so a misconfigured large
#: backoff value can't stall a worker for an unreasonable amount of time.
_MAX_BACKOFF_SECONDS = 10.0

#: Type alias for the callback invoked once per completed candidate URL.
#: The second argument is the raw header set of the *terminal* response
#: (for persisting to the ``headers`` database table), or ``None`` if no
#: response was ever received.
ResultCallback = Callable[[RedirectResult, httpx.Headers | None], Awaitable[None]]


class EngineRequestError(Exception):
    """Raised when a request fails after exhausting all configured retries."""


class RateLimiter:
    """A simple, strict-pacing rate limiter shared across all workers.

    Every call to :meth:`acquire` is spaced at least ``1 / rate`` seconds
    apart from the previous one, bounding the *aggregate* request rate
    across the entire worker pool to ``rate`` requests/second — regardless
    of how many workers are configured.

    This is intentionally a strict pacer, not a bursty token bucket: it
    never allows a queue of built-up "credit" to be spent in a burst. For
    an auditing tool whose purpose includes being a good network citizen
    toward third-party sites under test, predictable pacing is preferable
    to burstiness.
    """

    def __init__(self, rate: float | None) -> None:
        self._interval = (1.0 / rate) if rate else 0.0
        self._lock = asyncio.Lock()
        self._next_slot = time.monotonic()

    async def acquire(self) -> None:
        """Block until the next request slot is available. No-op if unlimited."""
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
                self._next_slot += self._interval
            else:
                self._next_slot = now + self._interval


@dataclass(slots=True)
class EngineStats:
    """Live-updating counters for the currently running (or just-finished) scan.

    A single instance is created per :class:`Engine` and mutated in place
    as results complete, so a caller (e.g. ``cli.py``'s Rich progress
    display) can poll it from a separate coroutine while :meth:`Engine.run`
    is still in flight — the engine has no dependency on how, or whether,
    its progress is displayed.
    """

    total: int = 0
    processed: int = 0
    alive: int = 0
    dead: int = 0
    redirects: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def record(self, result: RedirectResult) -> None:
        """Update counters for one completed result."""
        self.processed += 1
        if result.alive:
            self.alive += 1
        else:
            self.dead += 1
        if result.redirect_type != RedirectType.NONE:
            self.redirects += 1
        if result.error:
            self.errors += 1

    @property
    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since this stats object was created."""
        return time.monotonic() - self.started_at

    @property
    def requests_per_second(self) -> float:
        """Rolling average throughput in completed URLs/second."""
        elapsed = self.elapsed_seconds
        return self.processed / elapsed if elapsed > 0 else 0.0

    @property
    def eta_seconds(self) -> float | None:
        """Estimated remaining seconds, or ``None`` if throughput is not yet known."""
        rate = self.requests_per_second
        if rate <= 0 or self.total <= 0:
            return None
        remaining = max(self.total - self.processed, 0)
        return remaining / rate


def _decode_body(raw: bytes) -> str | None:
    """Decode a raw response body to text for HTML parsing.

    UTF-8 with error replacement is used unconditionally rather than
    attempting full charset sniffing from headers/meta tags: redirect
    detection only needs to find ASCII-safe patterns (``<meta
    http-equiv="refresh">``, ``window.location = "..."``), all of which
    survive UTF-8 decoding with replacement even when the *declared*
    encoding is something else, since the pattern-relevant text is invariably
    ASCII regardless of the surrounding document's real encoding.
    """
    if not raw:
        return None
    return raw.decode("utf-8", errors="replace")


class Engine:
    """Async scanning engine: worker pool + HTTP client + redirect-following loop.

    Constructed with a resolved :class:`~redirecthunter.models.ScanConfig`
    and an injectable :class:`~redirecthunter.analyzer.ResponseAnalyzer`
    (defaults to a standard one if not supplied), matching the project's
    dependency-injection requirement — tests can supply a fake analyzer
    without needing a real network.
    """

    def __init__(self, config: ScanConfig, analyzer: ResponseAnalyzer | None = None) -> None:
        self._config = config
        self._analyzer = analyzer or ResponseAnalyzer()
        self._rate_limiter = RateLimiter(config.rate_limit)
        self.stats = EngineStats()

    def _build_client(self) -> httpx.AsyncClient:
        """Construct the single shared, connection-pooled HTTP client for this scan.

        One client (one connection pool) is shared by every worker rather
        than one client per worker — this is what keeps 500 concurrent
        workers memory- and socket-efficient instead of each maintaining
        its own independent pool.
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
            follow_redirects=False,  # RedirectHunter always drives its own hop loop
            verify=self._config.verify_tls,
            proxy=self._config.proxy,
            headers=headers,
        )

    async def _send_request(
        self, client: httpx.AsyncClient, method: str, url: str
    ) -> tuple[httpx.Response, bytes]:
        """Send a single HTTP request, returning the response and a (possibly capped) body.

        HEAD requests never have a body and are sent directly. GET requests
        are streamed so the connection can be closed as soon as
        :data:`MAX_BODY_BYTES` is reached, rather than buffering an
        unbounded response in memory.
        """
        if method == "HEAD":
            response = await client.request(method, url)
            return response, b""

        request = client.build_request(method, url)
        response = await client.send(request, stream=True)
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

    async def _request_with_retry(
        self, client: httpx.AsyncClient, method: str, url: str
    ) -> tuple[httpx.Response, bytes]:
        """Send a request, retrying transport-level failures with exponential backoff.

        Only network/transport errors (``httpx.RequestError`` — connection
        refused, DNS failure, timeout, TLS failure, etc.) trigger a retry.
        HTTP error status codes (4xx/5xx) are never retried here — they are
        a valid, meaningful response the analyzer needs to see, not a
        failure to recover from.
        """
        attempts = self._config.retry + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._send_request(client, method, url)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    delay = min(self._config.retry_backoff * (2**attempt), _MAX_BACKOFF_SECONDS)
                    await asyncio.sleep(delay)
        raise EngineRequestError(f"{type(last_exc).__name__}: {last_exc}") from last_exc

    async def _run_chain(
        self, client: httpx.AsyncClient, source_url: str, expanded_url: str
    ) -> tuple[RedirectResult, httpx.Headers | None]:
        """Fetch and follow one candidate URL's redirect chain to completion.

        Drives the fetch -> analyze -> decide-whether-to-continue loop,
        bounded by ``max_redirects``, and delegates all classification to
        :class:`~redirecthunter.analyzer.ResponseAnalyzer`.

        Returns:
            A tuple of the assembled ``RedirectResult`` and the raw
            ``httpx.Headers`` of the *terminal* response (for the
            ``headers`` database table), or ``None`` for the headers if no
            response was ever received.
        """
        method = self._config.method.value
        chain: list[RedirectHop] = []
        terminal_analysis: HopAnalysis | None = None
        terminal_headers: httpx.Headers | None = None
        error: str | None = None
        alive = False
        current_url = expanded_url
        chain_start = time.monotonic()
        max_hops = self._config.max_redirects

        for hop_index in range(max_hops + 1):
            await self._rate_limiter.acquire()
            hop_start = time.monotonic()
            try:
                response, raw_body = await self._request_with_retry(client, method, current_url)
            except EngineRequestError as exc:
                error = str(exc)
                break

            hop_latency_ms = (time.monotonic() - hop_start) * 1000
            alive = True
            body_text = _decode_body(raw_body)

            analysis = self._analyzer.analyze_hop(
                url=str(response.url),
                status_code=response.status_code,
                headers=response.headers,
                body_text=body_text,
                hop_index=hop_index,
                latency_ms=hop_latency_ms,
                set_cookie_values=response.headers.get_list("set-cookie"),
            )

            can_continue = (
                analysis.next_url is not None
                and self._config.follow_redirects
                and hop_index < max_hops
            )
            if can_continue:
                chain.append(analysis.hop)
                current_url = analysis.next_url  # type: ignore[assignment]
                continue

            terminal_analysis = analysis
            terminal_headers = response.headers
            break

        total_latency_ms = (time.monotonic() - chain_start) * 1000
        result = self._analyzer.build_result(
            scan_id=self._config.scan_id,
            source_url=source_url,
            expanded_url=expanded_url,
            http_method=self._config.method,
            redirect_chain=chain,
            terminal_hop_analysis=terminal_analysis,
            total_latency_ms=total_latency_ms,
            alive=alive,
            error=error,
        )
        return result, terminal_headers

    async def _process_candidate(
        self,
        client: httpx.AsyncClient,
        candidate: CandidateURL,
        on_result: ResultCallback,
    ) -> None:
        """Expand, scan, and report one candidate URL, never raising back to the worker loop.

        A single misbehaving candidate (missing target, malformed URL,
        unexpected exception deep in analysis) must never take down the
        worker processing it — one bad row in a 100k-URL input file should
        produce one error result, not abort the scan.
        """
        try:
            expanded_url = expand_target(candidate.raw_url, self._config.target)
        except MissingTargetError as exc:
            result = RedirectResult(
                scan_id=self._config.scan_id,
                source_url=candidate.raw_url,
                expanded_url=candidate.raw_url,
                http_method=self._config.method,
                alive=False,
                latency_ms=0.0,
                error=str(exc),
            )
            self.stats.record(result)
            await on_result(result, None)
            return

        terminal_headers: httpx.Headers | None = None
        try:
            result, terminal_headers = await self._run_chain(client, candidate.raw_url, expanded_url)
        except Exception as exc:  # noqa: BLE001 - a single candidate must never abort the scan
            result = RedirectResult(
                scan_id=self._config.scan_id,
                source_url=candidate.raw_url,
                expanded_url=expanded_url,
                http_method=self._config.method,
                alive=False,
                latency_ms=0.0,
                error=f"Unexpected error: {type(exc).__name__}: {exc}",
            )

        self.stats.record(result)
        await on_result(result, terminal_headers)

    async def run(
        self,
        candidates: Iterable[CandidateURL] | AsyncIterator[CandidateURL],
        on_result: ResultCallback,
        total: int | None = None,
    ) -> EngineStats:
        """Scan every candidate URL with bounded concurrency and memory.

        Uses a producer/consumer pattern: one producer coroutine feeds a
        bounded ``asyncio.Queue`` from ``candidates`` (which may itself be
        a lazy generator reading from disk), and ``config.workers``
        consumer coroutines drain it concurrently. The queue's bounded size
        (``workers * 4``) ensures that even a 100k-URL input file is never
        fully materialized in memory at once — at most a small multiple of
        ``workers`` candidates are buffered ahead of the workers actually
        processing them.

        Args:
            candidates: An iterable (sync or async) of candidate URLs.
            on_result: Async callback invoked once per completed candidate,
                successful or not. Typically wired to
                ``Database.save_result`` plus a progress-bar update by the
                caller.
            total: Total candidate count, if known in advance, used only
                to populate ``EngineStats.total`` for ETA calculation.

        Returns:
            The final :class:`EngineStats` for this run.
        """
        self.stats = EngineStats(total=total or 0)
        queue: asyncio.Queue[CandidateURL | None] = asyncio.Queue(maxsize=self._config.workers * 4)

        async def producer() -> None:
            if hasattr(candidates, "__aiter__"):
                async for candidate in candidates:  # type: ignore[union-attr]
                    await queue.put(candidate)
            else:
                for candidate in candidates:  # type: ignore[union-attr]
                    await queue.put(candidate)
            for _ in range(self._config.workers):
                await queue.put(None)

        async def worker(client: httpx.AsyncClient) -> None:
            while True:
                candidate = await queue.get()
                try:
                    if candidate is None:
                        return
                    await self._process_candidate(client, candidate, on_result)
                finally:
                    queue.task_done()

        async with self._build_client() as client:
            producer_task = asyncio.create_task(producer())
            worker_tasks = [asyncio.create_task(worker(client)) for _ in range(self._config.workers)]
            await producer_task
            await asyncio.gather(*worker_tasks)

        return self.stats


__all__ = ["Engine", "EngineStats", "EngineRequestError", "RateLimiter", "MAX_BODY_BYTES", "ResultCallback"]
