"""Tests for redirecthunter.detector."""

from __future__ import annotations

from redirecthunter.detector import RedirectDetector, default_detector
from redirecthunter.models import RedirectType
from redirecthunter.plugins.base import DetectionContext
from redirecthunter.plugins.http_location import HttpLocationPlugin


class TestRedirectDetector:
    def test_default_pipeline_order(self) -> None:
        detector = default_detector()
        assert detector.plugin_names == ["http_location", "meta_refresh", "javascript"]

    def test_http_location_wins_over_simultaneous_meta_refresh(self) -> None:
        detector = default_detector()
        body = '<meta http-equiv="refresh" content="0;url=https://meta-target.com">'
        ctx = DetectionContext(
            url="https://a.com/go", status_code=301, headers={"Location": "https://header-target.com"}, body_text=body
        )
        outcome, _ = detector.analyze(ctx)
        assert outcome.redirect_type == RedirectType.HTTP_301
        assert outcome.destination == "https://header-target.com"

    def test_meta_refresh_wins_over_simultaneous_js(self) -> None:
        detector = default_detector()
        body = (
            '<meta http-equiv="refresh" content="0;url=https://meta-target.com">'
            '<script>window.location="https://js-target.com";</script>'
        )
        ctx = DetectionContext(url="https://a.com", status_code=200, headers={}, body_text=body)
        outcome, _ = detector.analyze(ctx)
        assert outcome.redirect_type == RedirectType.META_REFRESH

    def test_no_redirect_but_cloudflare_still_classified(self) -> None:
        detector = default_detector()
        ctx = DetectionContext(
            url="https://a.com", status_code=200, headers={"CF-RAY": "abc"}, body_text="<p>hi</p>"
        )
        outcome, cf_status = detector.analyze(ctx)
        assert outcome is None
        assert cf_status.is_cloudflare is True

    def test_dependency_injection_custom_pipeline(self) -> None:
        custom = RedirectDetector(plugins=[HttpLocationPlugin()])
        assert custom.plugin_names == ["http_location"]
        body = '<script>window.location="https://x.com";</script>'
        ctx = DetectionContext(url="https://a.com", status_code=200, headers={}, body_text=body)
        assert custom.detect(ctx) is None  # JS plugin not in this custom pipeline
