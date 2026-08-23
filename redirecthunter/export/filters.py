"""Row-level export/show filters, shared between ``export`` and ``show``.

Kept separate from the writers (:mod:`redirecthunter.export.csv_writer`,
:mod:`redirecthunter.export.json_writer`) and from :class:`~redirecthunter.export.service.Exporter`
so ``cli.py``'s ``show`` command can reuse :class:`ExportFilter` for its own
in-terminal filtering without importing anything writer-specific.
"""

from __future__ import annotations

from dataclasses import dataclass

from redirecthunter.models import RedirectResult


class ExportError(Exception):
    """Raised when an export cannot be completed."""


@dataclass(frozen=True, slots=True)
class ExportFilter:
    """Optional row filters applied while streaming a scan's results out.

    Mirrors the filter semantics of ``redirecthunter show`` (see
    ``cli.py``) so the same flag means the same thing whether the operator
    is looking at the terminal table or exporting to a file. All fields
    default to ``False``/empty (no filtering, matching current behavior)
    so existing callers of :meth:`~redirecthunter.export.service.Exporter.export`
    are unaffected.

    ``has_link_only`` is new here (not present on ``show``): it isolates
    results whose terminal response body contained a navigable ``<a
    href>`` -- e.g. manual "click here to continue" interstitials --
    which is a distinct, and sometimes non-overlapping, question from
    "did this URL redirect" (``redirects_only``): a page can have a body
    link with no HTTP/meta/JS redirect at all, or vice versa. Note that
    ``body_link`` can only ever be populated on responses that had a body
    to inspect in the first place -- a scan run with ``--method HEAD``
    (the default) never has one, so ``--has-link-only`` will always
    export zero rows for a HEAD-only scan. Re-run (or resume) the scan
    with ``--method GET`` if you need this field.

    ``status_codes`` / ``status_classes`` restrict by HTTP status: an
    exact code (e.g. ``301``) or a whole response class (e.g. ``3`` for
    every 3xx). A row passes this filter if it matches *any* requested
    code or class -- the two collections are OR'd together, then that
    combined result is AND'd with every other active filter, same as
    ``redirecthunter show --status-code``.
    """

    alive_only: bool = False
    redirects_only: bool = False
    cloudflare_only: bool = False
    has_link_only: bool = False
    status_codes: frozenset[int] = frozenset()
    status_classes: frozenset[int] = frozenset()

    @property
    def is_empty(self) -> bool:
        """True if no filter is active (a plain, unfiltered export)."""
        return not (
            self.alive_only
            or self.redirects_only
            or self.cloudflare_only
            or self.has_link_only
            or self.status_codes
            or self.status_classes
        )

    def matches(self, result: RedirectResult) -> bool:
        """Return True if ``result`` passes every active filter."""
        if self.alive_only and not result.alive:
            return False
        if self.redirects_only and result.redirect_type.value == "none":
            return False
        if self.cloudflare_only and not result.fingerprint.cloudflare.is_cloudflare:
            return False
        if self.has_link_only and not result.body_link:
            return False
        if self.status_codes or self.status_classes:
            code = result.status_code
            if code is None:
                return False
            if code not in self.status_codes and (code // 100) not in self.status_classes:
                return False
        return True


__all__ = ["ExportError", "ExportFilter"]
