"""Inline JavaScript redirect detector.

Detects static, string-literal redirect targets in inline ``<script>``
tags: ``window.location``/``document.location`` assignments and
``location.assign()``/``location.replace()`` calls. This is intentionally
a **static pattern matcher**, not a JavaScript engine — RedirectHunter
never executes page script. Redirects whose destination is computed at
runtime (e.g. built from a variable rather than a string literal) are not
detectable this way and are correctly reported as "no JS redirect found"
rather than guessed at.
"""

from __future__ import annotations

import re

from redirecthunter.models import DetectionOutcome, RedirectType
from redirecthunter.plugins.base import DetectionContext, RedirectDetectorPlugin
from redirecthunter.utils import truncate_text

#: Matches "location", "window.location", or "document.location" at a real
#: word boundary — the leading \b prevents accidental matches inside
#: unrelated identifiers such as "geolocation" or "relocationService".
_LOCATION_ROOT = r"\b(?:window\.location|document\.location|location)"

#: Two alternatives: a method call with a string-literal argument
#: (``location.assign("...")`` / ``location.replace("...")``), or a direct
#: assignment to a string literal (``location = "..."`` /
#: ``location.href = "..."``). Both window./document./bare-prefixed forms
#: are covered via ``_LOCATION_ROOT``.
_JS_REDIRECT_PATTERN = re.compile(
    rf"{_LOCATION_ROOT}\.(?:assign|replace)\s*\(\s*['\"](?P<call_url>[^'\"]+)['\"]\s*\)"
    rf"|{_LOCATION_ROOT}(?:\.href)?\s*=\s*['\"](?P<assign_url>[^'\"]+)['\"]",
    re.IGNORECASE,
)

_EVIDENCE_MAX_LENGTH = 160


class JavaScriptRedirectPlugin(RedirectDetectorPlugin):
    """Detects inline JS redirects via static string-literal pattern matching."""

    name = "javascript"

    def detect(self, context: DetectionContext) -> DetectionOutcome | None:
        """Scan inline scripts (document order) for the first redirect pattern match.

        External scripts (``<script src="...">``) are skipped entirely —
        RedirectHunter does not fetch or execute referenced JS files, only
        inline code already present in the HTML response.
        """
        tree = context.html_tree
        if tree is None:
            return None

        for script in tree.css("script"):
            if script.attributes.get("src"):
                continue

            script_text = script.text()
            if not script_text or "location" not in script_text.lower():
                continue

            match = _JS_REDIRECT_PATTERN.search(script_text)
            if not match:
                continue

            destination = (match.group("call_url") or match.group("assign_url") or "").strip()
            if not destination:
                continue

            evidence = truncate_text(match.group(0).strip(), _EVIDENCE_MAX_LENGTH)

            return DetectionOutcome(
                redirect_type=RedirectType.JAVASCRIPT,
                destination=destination,
                confidence=0.9,
                source=self.name,
                raw_evidence=evidence,
            )

        return None


__all__ = ["JavaScriptRedirectPlugin"]
