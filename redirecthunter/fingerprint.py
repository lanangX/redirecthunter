"""Server / CDN fingerprinting derived from HTTP response headers.

This module performs passive, header-only (plus an optional small body
sample for Cloudflare challenge-page detection) classification of the
technology serving a response. It never sends extra requests and never
attempts to defeat any protection it detects — Cloudflare classification in
particular is intentionally limited to *flagging* protected targets, per
the project's "classify, don't bypass" requirement.

:mod:`redirecthunter.plugins.cloudflare` builds on top of
:func:`detect_cloudflare` here, adding full-page HTML inspection when a
GET (not HEAD) response body is available.
"""

from __future__ import annotations

from collections.abc import Mapping

from redirecthunter.models import CloudflareStatus, FingerprintInfo

#: Path fragment injected by Cloudflare into HTML for its edge features
#: (challenge scripts, RUM beacon, email obfuscation, etc.).
CDN_CGI_PATH_MARKER = "/cdn-cgi/"

#: Case-insensitive substrings found in the body of a Cloudflare
#: interstitial/challenge page (JS challenge, managed challenge, CAPTCHA).
#: Kept intentionally minimal — this is a classifier, not an evasion tool.
_CHALLENGE_PAGE_MARKERS: tuple[str, ...] = (
    "checking your browser before accessing",
    "cf-browser-verification",
    "cf_chl_opt",
    "jschl-answer",
    "cdn-cgi/challenge-platform",
    "attention required! | cloudflare",
    "just a moment...",
)


def _lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a plain dict of headers with lowercase keys.

    Accepts either an ``httpx.Headers`` instance (already case-insensitive)
    or a plain ``dict`` (e.g. in unit tests) and normalizes both to the
    same lookup semantics.
    """
    return {str(key).lower(): str(value) for key, value in headers.items()}


def detect_cloudflare(
    headers: Mapping[str, str],
    cookies: Mapping[str, str] | None = None,
    body_sample: str | None = None,
) -> CloudflareStatus:
    """Classify Cloudflare presence from headers, cookies, and an optional body sample.

    Args:
        headers: Response headers (case-insensitive lookup).
        cookies: Parsed response cookies, e.g. from
            :func:`redirecthunter.utils.parse_set_cookie_headers`.
        body_sample: A (possibly truncated) decoded response body, used only
            to detect the ``/cdn-cgi/`` path marker and challenge-page
            copy. Pass ``None`` for HEAD requests where no body exists.

    Returns:
        A :class:`~redirecthunter.models.CloudflareStatus`. All fields
        default to ``False``/``None`` when no signal is present.
    """
    h = _lower_headers(headers)
    cookies = cookies or {}

    has_cf_ray = "cf-ray" in h
    has_cf_cache_status = "cf-cache-status" in h
    has_cf_clearance_cookie = "cf_clearance" in cookies
    server = h.get("server", "")

    has_cdn_cgi_path = bool(body_sample) and CDN_CGI_PATH_MARKER in (body_sample or "")

    is_challenge_page = False
    if body_sample:
        lowered = body_sample.lower()
        is_challenge_page = any(marker in lowered for marker in _CHALLENGE_PAGE_MARKERS)

    is_cloudflare = (
        has_cf_ray
        or has_cf_cache_status
        or has_cf_clearance_cookie
        or "cloudflare" in server.lower()
        or has_cdn_cgi_path
    )

    return CloudflareStatus(
        is_cloudflare=is_cloudflare,
        has_cf_ray=has_cf_ray,
        has_cf_cache_status=has_cf_cache_status,
        has_cf_clearance_cookie=has_cf_clearance_cookie,
        has_cdn_cgi_path=has_cdn_cgi_path,
        is_challenge_page=is_challenge_page,
        cf_ray_id=h.get("cf-ray"),
    )


def detect_software(headers: Mapping[str, str]) -> str | None:
    """Classify the origin/edge software serving a response from its headers.

    Checks are ordered from most to least specific: dedicated CDN vendors
    (Cloudflare, CloudFront, Fastly, Akamai) are checked before the generic
    reverse proxies they're commonly built on (Varnish), which are in turn
    checked before origin web servers (nginx, Apache, LiteSpeed, IIS) — a
    Fastly-fronted origin should be reported as "Fastly", not "Varnish".

    Args:
        headers: Response headers (case-insensitive lookup).

    Returns:
        A short vendor/software label, or ``None`` if no known signature
        matched.
    """
    h = _lower_headers(headers)
    server = h.get("server", "").lower()
    via = h.get("via", "").lower()

    if "cloudflare" in server or "cf-ray" in h:
        return "Cloudflare"

    if "cloudfront" in server or "x-amz-cf-id" in h or "x-amz-cf-pop" in h:
        return "CloudFront"

    if "x-fastly-request-id" in h or "fastly-debug-path" in h or "fastly" in via:
        return "Fastly"

    if "akamaighost" in server or "akamai" in server or any(k.startswith("x-akamai") for k in h):
        return "Akamai"

    if "x-varnish" in h or "varnish" in via:
        return "Varnish"

    if "litespeed" in server:
        return "LiteSpeed"

    if "nginx" in server:
        return "nginx"

    if "microsoft-iis" in server or ("iis" in server and "microsoft" in server):
        return "IIS"

    if "apache" in server:
        return "Apache"

    return None


def build_fingerprint(
    headers: Mapping[str, str],
    cookies: Mapping[str, str] | None = None,
    body_sample: str | None = None,
) -> FingerprintInfo:
    """Build a complete :class:`FingerprintInfo` from a response.

    Combines :func:`detect_software` and :func:`detect_cloudflare` into the
    single fingerprint object stored on every
    :class:`~redirecthunter.models.RedirectResult`.

    Args:
        headers: Response headers.
        cookies: Parsed response cookies.
        body_sample: Optional decoded body sample (used for Cloudflare
            challenge-page and ``/cdn-cgi/`` detection only).

    Returns:
        A populated ``FingerprintInfo``.
    """
    h = _lower_headers(headers)
    cloudflare_status = detect_cloudflare(headers, cookies, body_sample)
    detected_software = detect_software(headers)

    if detected_software is None and cloudflare_status.is_cloudflare:
        detected_software = "Cloudflare"

    return FingerprintInfo(
        server_header=h.get("server"),
        powered_by_header=h.get("x-powered-by"),
        detected_software=detected_software,
        cloudflare=cloudflare_status,
    )


__all__ = [
    "CDN_CGI_PATH_MARKER",
    "detect_cloudflare",
    "detect_software",
    "build_fingerprint",
]
