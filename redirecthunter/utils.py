"""Shared, dependency-light helper functions used across RedirectHunter.

Nothing in this module depends on any other RedirectHunter module — it sits
at the bottom of the import graph so ``detector.py``, ``analyzer.py``,
``engine.py``, and the plugins can all depend on it without risking a
circular import.
"""

from __future__ import annotations

from collections.abc import Iterable
from http.cookies import SimpleCookie
from urllib.parse import quote, urljoin, urlparse, urlunparse

#: The literal placeholder token that candidate-URL templates use to mark
#: where the operator-supplied target should be substituted.
TARGET_PLACEHOLDER = "{TARGET}"

#: Schemes RedirectHunter will treat as valid, resolvable web targets.
_VALID_SCHEMES = frozenset({"http", "https"})


class MissingTargetError(ValueError):
    """Raised when a URL template contains {TARGET} but no target was supplied."""


def contains_target_placeholder(template: str) -> bool:
    """Return True if the given URL template contains the {TARGET} token."""
    return TARGET_PLACEHOLDER in template


def expand_target(template: str, target: str | None, *, url_encode: bool = False) -> str:
    """Substitute the {TARGET} placeholder in a candidate URL template.

    Args:
        template: A candidate URL, e.g. ``https://example.com/go?url={TARGET}``.
        target: The operator-supplied replacement value, e.g.
            ``https://example.org``. If the template contains no placeholder,
            this argument is ignored and the template is returned unchanged.
        url_encode: If True, percent-encode ``target`` before substitution
            (safe for embedding an arbitrary URL inside a query-string
            parameter). Defaults to False to match the literal, faithful
            substitution behavior described in the project specification —
            most open-redirect parameters expect a raw URL, not a
            double-encoded one.

    Returns:
        The template with every occurrence of ``{TARGET}`` replaced.

    Raises:
        MissingTargetError: If the template contains ``{TARGET}`` but
            ``target`` is ``None`` or empty.
    """
    if not contains_target_placeholder(template):
        return template

    if not target:
        raise MissingTargetError(
            f"URL template '{template}' contains {TARGET_PLACEHOLDER} but no --target was supplied."
        )

    value = quote(target, safe="") if url_encode else target
    return template.replace(TARGET_PLACEHOLDER, value)


def normalize_url(url: str, *, default_scheme: str = "https") -> str:
    """Normalize a URL for consistent comparison and requesting.

    Performs, in order:
        1. Whitespace stripping.
        2. Scheme inference for bare hostnames (``example.com`` ->
           ``https://example.com``).
        3. Lowercasing of scheme and host (paths/query stay case-sensitive,
           per RFC 3986 — many servers treat path case meaningfully).
        4. Stripping of default ports (``:80`` on http, ``:443`` on https).

    Args:
        url: The raw URL string.
        default_scheme: Scheme to assume when none is present.

    Returns:
        The normalized URL string. Malformed input is returned stripped
        but otherwise unmodified rather than raising, since callers are
        expected to validate with :func:`is_valid_http_url` separately.
    """
    candidate = url.strip()
    if not candidate:
        return candidate

    if "://" not in candidate:
        candidate = f"{default_scheme}://{candidate}"

    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    host = host.lower()

    port = parsed.port
    default_port = {"http": 80, "https": 443}.get(scheme)
    netloc = host
    if port is not None and port != default_port:
        netloc = f"{host}:{port}"

    if parsed.username:
        credentials = parsed.username
        if parsed.password:
            credentials += f":{parsed.password}"
        netloc = f"{credentials}@{netloc}"

    normalized = urlunparse(
        (scheme, netloc, parsed.path or "", parsed.params, parsed.query, parsed.fragment)
    )
    return normalized


def resolve_relative_url(base_url: str, location: str) -> str:
    """Resolve a possibly-relative redirect target against its originating URL.

    Handles the common real-world cases: absolute URLs, protocol-relative
    URLs (``//cdn.example.com/x``), absolute paths (``/login``), and
    relative paths (``../x``).

    Args:
        base_url: The URL that produced the redirect (the request URL of
            the hop, not necessarily the original source URL).
        location: The raw ``Location`` header, meta-refresh destination, or
            JavaScript redirect target.

    Returns:
        A fully-qualified absolute URL.
    """
    return urljoin(base_url, location.strip())


def is_valid_http_url(url: str) -> bool:
    """Return True if ``url`` is a well-formed http(s) URL with a hostname."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme.lower() in _VALID_SCHEMES and bool(parsed.hostname)


def extract_domain(url: str) -> str | None:
    """Extract the lowercase hostname from a URL, or None if unparseable."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    return parsed.hostname.lower() if parsed.hostname else None


def is_external_domain(url: str, reference_domain: str) -> bool:
    """Return True if ``url``'s hostname is neither ``reference_domain`` nor a subdomain of it.

    Hostname-based, not substring-based — this deliberately avoids the
    classic false-positive of naive matching, e.g. a plain
    ``reference_domain in url`` check would wrongly treat
    ``https://notexample.org.evil.com`` as "belonging to" ``example.org``.
    A URL with no parseable hostname is treated as not external (there is
    nothing to compare).

    Args:
        url: The URL to classify, typically a scan result's ``final_url``.
        reference_domain: The domain to compare against, e.g. the
            operator's ``--target`` domain. A leading ``www.`` is ignored.

    Returns:
        True if the URL's host is outside ``reference_domain``.
    """
    host = extract_domain(url)
    if not host:
        return False
    reference = reference_domain.lower()
    if reference.startswith("www."):
        reference = reference[4:]
    return not (host == reference or host.endswith(f".{reference}"))


def dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    """Deduplicate a sequence of strings while preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def parse_set_cookie_headers(values: Iterable[str]) -> dict[str, str]:
    """Parse one or more raw ``Set-Cookie`` header values into a name->value dict.

    Malformed individual cookie strings are skipped rather than raising, so
    one bad cookie from a misbehaving server doesn't abort result collection
    for the whole request.

    Args:
        values: Raw ``Set-Cookie`` header strings (one per cookie, as httpx
            exposes them via ``response.headers.get_list("set-cookie")``).

    Returns:
        A flat mapping of cookie name to cookie value. Attributes such as
        ``Path``, ``Domain``, and ``Expires`` are intentionally discarded —
        RedirectHunter only records presence/value for audit purposes, not
        full cookie-jar semantics.
    """
    result: dict[str, str] = {}
    for raw in values:
        try:
            jar: SimpleCookie = SimpleCookie()
            jar.load(raw)
        except Exception:  # noqa: BLE001 - defensive parsing, never fatal
            continue
        for name, morsel in jar.items():
            result[name] = morsel.value
    return result


def truncate_text(text: str, max_length: int = 200) -> str:
    """Truncate text to at most ``max_length`` characters, appending an ellipsis.

    Used when storing "evidence" snippets (matched HTML/JS fragments) so a
    single pathological page can't bloat the results database.
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "\u2026"


def format_bytes(size: int | None) -> str:
    """Format a byte count as a short human-readable string (e.g. '1.4 KB')."""
    if size is None:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def format_latency(latency_ms: float) -> str:
    """Format a millisecond latency as a short human-readable string."""
    if latency_ms < 1000:
        return f"{latency_ms:.0f} ms"
    return f"{latency_ms / 1000:.2f} s"


__all__ = [
    "TARGET_PLACEHOLDER",
    "MissingTargetError",
    "contains_target_placeholder",
    "expand_target",
    "normalize_url",
    "resolve_relative_url",
    "is_valid_http_url",
    "extract_domain",
    "is_external_domain",
    "dedupe_preserve_order",
    "parse_set_cookie_headers",
    "truncate_text",
    "format_bytes",
    "format_latency",
]
