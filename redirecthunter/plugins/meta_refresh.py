"""``<meta http-equiv="refresh">`` redirect detector.

Runs after :mod:`http_location` in the pipeline and only inspects the
parsed HTML body — so it correctly does nothing on HEAD responses (no
body) or on non-HTML content types (no ``<meta>`` tags found).
"""

from __future__ import annotations

import re

from redirecthunter.models import DetectionOutcome, RedirectType
from redirecthunter.plugins.base import DetectionContext, RedirectDetectorPlugin

#: Matches the "url=<destination>" portion of a meta-refresh ``content``
#: attribute, e.g. ``5;url=https://example.com`` or
#: ``0; URL='https://example.com/path'``. The delay digits before the
#: semicolon are intentionally not captured — only the destination matters.
_CONTENT_URL_PATTERN = re.compile(r"url\s*=\s*(?P<url>.+)$", re.IGNORECASE)


class MetaRefreshPlugin(RedirectDetectorPlugin):
    """Detects ``<meta http-equiv="refresh" content="...;url=...">`` redirects."""

    name = "meta_refresh"

    def detect(self, context: DetectionContext) -> DetectionOutcome | None:
        """Return a DetectionOutcome for the first meta-refresh tag with a URL.

        A meta-refresh with no ``url=`` component (e.g. ``content="5"``) is
        a self-reload, not a redirect, and is correctly skipped. If a page
        somehow contains multiple meta-refresh tags, only the first
        (document order) is reported, matching real browser behavior.
        """
        tree = context.html_tree
        if tree is None:
            return None

        for meta in tree.css("meta"):
            http_equiv = meta.attributes.get("http-equiv")
            if not http_equiv or http_equiv.strip().lower() != "refresh":
                continue

            content = meta.attributes.get("content")
            if not content:
                continue

            destination = self._extract_destination(content)
            if destination is None:
                continue

            return DetectionOutcome(
                redirect_type=RedirectType.META_REFRESH,
                destination=destination,
                confidence=1.0,
                source=self.name,
                raw_evidence=f'<meta http-equiv="refresh" content="{content}">',
            )

        return None

    @staticmethod
    def _extract_destination(content: str) -> str | None:
        """Parse the destination URL out of a meta-refresh ``content`` value.

        Handles delay-only values (``"5"`` -> None), quoted URLs
        (``url='...'`` / ``url="..."``), unquoted URLs, and trailing
        semicolons/whitespace some CMSs emit.
        """
        match = _CONTENT_URL_PATTERN.search(content)
        if not match:
            return None

        url = match.group("url").strip()
        url = url.strip("'\"")
        url = url.rstrip(";").strip()
        return url or None


__all__ = ["MetaRefreshPlugin"]
