"""Cloudflare protection classifier.

Unlike the other plugins in this package, Cloudflare presence is
**orthogonal** to which redirect type (if any) was found on a response — a
Cloudflare-fronted origin can return a 301, a meta-refresh, a JS redirect,
or a plain 200, independently of whether Cloudflare itself is present. For
that reason this module does not implement the
:class:`~redirecthunter.plugins.base.RedirectDetectorPlugin` contract;
:mod:`redirecthunter.detector` invokes :meth:`CloudflarePlugin.classify`
once per response, separately from the redirect-type pipeline, and attaches
the result to ``RedirectResult.fingerprint.cloudflare``.

RedirectHunter only classifies Cloudflare protection. It never attempts to
solve a JS challenge, defeat a CAPTCHA, or forge a ``cf_clearance`` cookie.
"""

from __future__ import annotations

from redirecthunter.fingerprint import detect_cloudflare
from redirecthunter.models import CloudflareStatus
from redirecthunter.plugins.base import DetectionContext
from redirecthunter.utils import truncate_text

#: Body sample size cap for challenge-page marker scanning. Cloudflare
#: interstitial pages are small (typically a few KB); capping bounds
#: worst-case cost on pathologically large HTML responses without
#: affecting detection accuracy, since challenge markers appear near the
#: top of the document (title/head).
_BODY_SAMPLE_LIMIT = 50_000


class CloudflarePlugin:
    """Classifies whether a response is Cloudflare-protected.

    Intentionally does **not** subclass ``RedirectDetectorPlugin`` — its
    output (:class:`~redirecthunter.models.CloudflareStatus`) is an
    annotation, not a redirect-type detection, and forcing it into the
    ``detect() -> DetectionOutcome | None`` contract would misrepresent
    "this site sits behind Cloudflare" as if it were itself a kind of
    redirect.
    """

    name = "cloudflare"

    def classify(self, context: DetectionContext) -> CloudflareStatus:
        """Classify Cloudflare protection from a response context.

        Args:
            context: The response data to inspect. Works correctly for
                HEAD responses (``body_text is None``) by falling back to
                header/cookie-only signals.

        Returns:
            A :class:`~redirecthunter.models.CloudflareStatus`. All
            boolean fields default to ``False`` when no signal is present.
        """
        body_sample = truncate_text(context.body_text, _BODY_SAMPLE_LIMIT) if context.body_text else None

        return detect_cloudflare(
            headers=context.headers,
            cookies=context.cookies,
            body_sample=body_sample,
        )


__all__ = ["CloudflarePlugin"]
