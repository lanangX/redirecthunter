"""Redirect-detection plugins for RedirectHunter.

Each module in this package implements one detection strategy for a single
:class:`~redirecthunter.models.RedirectType` family:

    - :mod:`http_location` — 301/302/303/307/308 + ``Location`` header.
    - :mod:`meta_refresh`  — ``<meta http-equiv="refresh">``.
    - :mod:`javascript`    — inline ``window.location`` / ``location.href``
      style redirects.
    - :mod:`cloudflare`    — Cloudflare protection classification (not a
      redirect-type plugin; annotates results rather than participating in
      the redirect-type detection pipeline).

See :mod:`redirecthunter.plugins.base` for the shared plugin contract and
:mod:`redirecthunter.detector` for pipeline orchestration.
"""
