"""HTTP status-code + ``Location`` header redirect detector.

The highest-priority plugin in the pipeline (see
:mod:`redirecthunter.detector`): a page can legitimately contain a
meta-refresh or JavaScript redirect snippet aimed at clients that ignore
the ``Location`` header, but the HTTP-level redirect is what real browsers
and HTTP clients actually follow, so it always takes precedence when
present.
"""

from __future__ import annotations

from redirecthunter.models import DetectionOutcome, RedirectType
from redirecthunter.plugins.base import DetectionContext, RedirectDetectorPlugin

#: Maps each redirect-capable HTTP status code to its RedirectType.
_STATUS_TO_REDIRECT_TYPE: dict[int, RedirectType] = {
    301: RedirectType.HTTP_301,
    302: RedirectType.HTTP_302,
    303: RedirectType.HTTP_303,
    307: RedirectType.HTTP_307,
    308: RedirectType.HTTP_308,
}


class HttpLocationPlugin(RedirectDetectorPlugin):
    """Detects 301/302/303/307/308 responses carrying a ``Location`` header."""

    name = "http_location"

    def detect(self, context: DetectionContext) -> DetectionOutcome | None:
        """Return a DetectionOutcome if the status code and Location header pair up.

        A redirect status code with a missing or empty ``Location`` header
        is treated as *no detection* (rather than raising) — some
        misconfigured servers do send this, and it's meaningful audit data
        for the analyzer to record as "redirect status without a target"
        rather than crash the pipeline.
        """
        redirect_type = _STATUS_TO_REDIRECT_TYPE.get(context.status_code)
        if redirect_type is None:
            return None

        location = context.get_header("location")
        if not location:
            return None

        location = location.strip()
        if not location:
            return None

        return DetectionOutcome(
            redirect_type=redirect_type,
            destination=location,
            confidence=1.0,
            source=self.name,
            raw_evidence=f"HTTP {context.status_code} Location: {location}",
        )


__all__ = ["HttpLocationPlugin"]
