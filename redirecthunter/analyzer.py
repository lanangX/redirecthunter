"""Response analysis: the bridge between raw HTTP data and structured models.

:mod:`redirecthunter.engine` owns the actual networking (issuing requests,
retries, rate limiting, concurrency) and the hop-following loop's control
flow. For every response it receives, it calls
:meth:`ResponseAnalyzer.analyze_hop` here to classify that single response.
Once the loop terminates (redirect chain exhausted, ``follow_redirects``
disabled, ``max_redirects`` hit, or a transport error occurred),
:meth:`ResponseAnalyzer.build_result` assembles the final, persistence-ready
:class:`~redirecthunter.models.RedirectResult`.

Redirect-chain semantics (this is the key design decision in this module):

    - ``redirect_chain`` contains only the responses that **were followed
      further** — i.e. actual redirects the engine chose to chase. It does
      **not** include the terminal response.
    - ``hop_count`` is therefore the number of redirects traversed (``0``
      for a direct 200, matching how most redirect-auditing tools report
      it — not the total number of HTTP requests made).
    - The top-level ``status_code`` / ``redirect_type`` / ``location``
      fields always describe the **first** response (``redirect_chain[0]``
      if any redirects were followed, otherwise the terminal response
      itself) — this answers "does this candidate URL redirect, and to
      what type/target?", which is the primary audit question.
    - ``final_url`` / ``server`` / ``content_type`` / ``content_length`` /
      ``cookies`` / ``fingerprint`` always describe the **terminal**
      response — the page actually landed on, which is what matters for
      verifying the destination is the intended/authorized target and for
      auditing what's actually serving it (e.g. Cloudflare-protected).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from redirecthunter.detector import RedirectDetector, default_detector
from redirecthunter.fingerprint import detect_software
from redirecthunter.models import (
    FingerprintInfo,
    HTTPMethod,
    RedirectHop,
    RedirectResult,
    RedirectType,
)
from redirecthunter.plugins.base import DetectionContext
from redirecthunter.utils import parse_set_cookie_headers, resolve_relative_url

#: Href prefixes that never point anywhere useful to an operator auditing
#: redirect destinations -- in-page anchors, script hooks, and non-web
#: schemes. Skipped so the first *navigable* link wins instead of, e.g.,
#: a "skip to content" accessibility anchor that happens to appear first
#: in document order.
_NON_NAVIGABLE_HREF_PREFIXES = ("#", "javascript:", "mailto:", "tel:")


def _extract_body_link(context: DetectionContext) -> str | None:
    """Return the raw ``href`` of the first navigable ``<a>`` tag in the body, if any.

    Reuses :attr:`DetectionContext.html_tree` -- the same lazily-parsed,
    cached selectolax tree the ``meta_refresh``/``javascript`` plugins
    already consult -- so this costs nothing extra on responses that were
    going to be parsed anyway, and correctly returns ``None`` for HEAD
    responses (no body) or non-HTML content (no tree).

    This is a passive observation, not a redirect-detection signal: it
    does not influence ``redirect_type`` classification or pipeline
    precedence. Many interstitial/"safe redirect" pages combine an
    automatic redirect (meta-refresh or JS) *with* a manual "click here"
    fallback link for browsers that block the automatic one -- operators
    auditing those pages want to see that fallback link even though the
    page already classified as ``meta_refresh``/``javascript``. It is
    just as useful on pages with *no* detected redirect at all: a bare
    "please click to continue" page that isn't a redirect by any of the
    other plugins' definitions, but is still an outbound link worth
    exporting.
    """
    tree = context.html_tree
    if tree is None:
        return None

    for anchor in tree.css("a"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        href = href.strip()
        if not href or href.lower().startswith(_NON_NAVIGABLE_HREF_PREFIXES):
            continue
        return href

    return None


@dataclass(slots=True)
class HopAnalysis:
    """Result of analyzing a single HTTP response within a (possible) redirect chain.

    Attributes:
        hop: The structured record of this response, suitable for the
            ``redirect_chain`` list if the engine decides to follow further.
        next_url: The fully-resolved absolute URL to fetch next, if a
            redirect was detected on this response; ``None`` otherwise.
        cookies: Parsed ``Set-Cookie`` values from this response.
        fingerprint: Server/CDN/Cloudflare fingerprint of this response.
        content_type: Raw ``Content-Type`` header value, if present.
        content_length: Parsed ``Content-Length`` header value, if present
            and numeric.
        body_link: Raw href of the first navigable ``<a>`` tag found in
            this response's body, if any. See :func:`_extract_body_link`.
    """

    hop: RedirectHop
    next_url: str | None
    cookies: dict[str, str]
    fingerprint: FingerprintInfo
    content_type: str | None
    content_length: int | None
    body_link: str | None = None


class ResponseAnalyzer:
    """Converts raw HTTP response data into RedirectHunter's structured models.

    Takes a :class:`~redirecthunter.detector.RedirectDetector` via
    constructor injection so callers (and tests) can supply a custom
    detection pipeline instead of the default one.
    """

    def __init__(self, detector: RedirectDetector | None = None) -> None:
        self._detector = detector or default_detector()

    def analyze_hop(
        self,
        *,
        url: str,
        status_code: int,
        headers: Mapping[str, str],
        body_text: str | None,
        hop_index: int,
        latency_ms: float,
        set_cookie_values: list[str] | None = None,
    ) -> HopAnalysis:
        """Classify a single HTTP response.

        Args:
            url: The URL that was requested to produce this response.
            status_code: HTTP status code of the response.
            headers: Response headers.
            body_text: Decoded response body, or ``None`` if unavailable
                (HEAD requests never have one; GET responses always do).
            hop_index: 0-based position of this response within the chain
                actually being built by the caller.
            latency_ms: Time taken for this specific request.
            set_cookie_values: Raw ``Set-Cookie`` header values, e.g. from
                ``httpx.Response.headers.get_list("set-cookie")``.

        Returns:
            A :class:`HopAnalysis`. The caller inspects ``next_url`` to
            decide whether to continue the chain or treat this response as
            terminal.
        """
        cookies = parse_set_cookie_headers(set_cookie_values or [])
        context = DetectionContext(
            url=url,
            status_code=status_code,
            headers=headers,
            body_text=body_text,
            cookies=cookies,
        )

        outcome, cloudflare_status = self._detector.analyze(context)

        redirect_type = outcome.redirect_type if outcome is not None else RedirectType.NONE
        next_url = resolve_relative_url(url, outcome.destination) if outcome is not None else None

        # detect_software() is called directly (rather than fingerprint.build_fingerprint())
        # to avoid recomputing Cloudflare classification a second time — it was already
        # produced by self._detector.analyze() above via the same DetectionContext.
        detected_software = detect_software(headers)
        if detected_software is None and cloudflare_status.is_cloudflare:
            detected_software = "Cloudflare"

        server_header = context.get_header("server")
        fingerprint = FingerprintInfo(
            server_header=server_header,
            powered_by_header=context.get_header("x-powered-by"),
            detected_software=detected_software,
            cloudflare=cloudflare_status,
        )

        hop = RedirectHop(
            hop_index=hop_index,
            url=url,
            status_code=status_code,
            redirect_type=redirect_type,
            location_header=context.get_header("location"),
            server_header=server_header,
            latency_ms=latency_ms,
        )

        content_length_raw = context.get_header("content-length")
        content_length = (
            int(content_length_raw)
            if content_length_raw is not None and content_length_raw.isdigit()
            else None
        )

        return HopAnalysis(
            hop=hop,
            next_url=next_url,
            cookies=cookies,
            fingerprint=fingerprint,
            content_type=context.get_header("content-type"),
            content_length=content_length,
            body_link=_extract_body_link(context),
        )

    def build_result(
        self,
        *,
        scan_id: str,
        source_url: str,
        expanded_url: str,
        http_method: HTTPMethod,
        redirect_chain: list[RedirectHop],
        terminal_hop_analysis: HopAnalysis | None,
        total_latency_ms: float,
        alive: bool,
        error: str | None = None,
    ) -> RedirectResult:
        """Assemble the final result once the hop-following loop has stopped.

        Args:
            scan_id: The scan this result belongs to.
            source_url: The raw candidate URL template ({TARGET} intact).
            expanded_url: ``source_url`` with {TARGET} substituted.
            http_method: The HTTP method used for every request in the chain.
            redirect_chain: Every redirect that was followed, in order.
                Empty if the terminal response was reached directly (no
                redirect), or if the request failed before any response
                arrived.
            terminal_hop_analysis: The analysis of the last response
                actually received, or ``None`` if the request failed
                (connection error, timeout, DNS failure, etc.) before any
                response arrived at all.
            total_latency_ms: Wall-clock time for the entire chain.
            alive: ``True`` if at least one response was received without
                a transport-level error.
            error: Transport/timeout error message, if any.

        Returns:
            The complete, persistence-ready ``RedirectResult``.
        """
        if terminal_hop_analysis is None:
            return RedirectResult(
                scan_id=scan_id,
                source_url=source_url,
                expanded_url=expanded_url,
                http_method=http_method,
                redirect_chain=redirect_chain,
                hop_count=len(redirect_chain),
                alive=alive,
                latency_ms=total_latency_ms,
                error=error,
            )

        terminal_hop = terminal_hop_analysis.hop
        first_hop = redirect_chain[0] if redirect_chain else terminal_hop

        return RedirectResult(
            scan_id=scan_id,
            source_url=source_url,
            expanded_url=expanded_url,
            http_method=http_method,
            status_code=first_hop.status_code,
            redirect_type=first_hop.redirect_type,
            location=first_hop.location_header,
            final_url=terminal_hop.url,
            body_link=terminal_hop_analysis.body_link,
            redirect_chain=redirect_chain,
            hop_count=len(redirect_chain),
            server=terminal_hop.server_header,
            content_type=terminal_hop_analysis.content_type,
            content_length=terminal_hop_analysis.content_length,
            cookies=terminal_hop_analysis.cookies,
            fingerprint=terminal_hop_analysis.fingerprint,
            alive=alive,
            latency_ms=total_latency_ms,
            error=error,
        )


__all__ = ["ResponseAnalyzer", "HopAnalysis"]
