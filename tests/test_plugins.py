"""Tests for redirecthunter.plugins.*"""

from __future__ import annotations

from redirecthunter.models import RedirectType
from redirecthunter.plugins.base import DetectionContext
from redirecthunter.plugins.cloudflare import CloudflarePlugin
from redirecthunter.plugins.http_location import HttpLocationPlugin
from redirecthunter.plugins.javascript import JavaScriptRedirectPlugin
from redirecthunter.plugins.meta_refresh import MetaRefreshPlugin


class TestDetectionContext:
    def test_case_insensitive_header_lookup(self) -> None:
        ctx = DetectionContext(url="https://a.com", status_code=200, headers={"X-Custom": "v"})
        assert ctx.get_header("x-custom") == "v"
        assert ctx.get_header("X-CUSTOM") == "v"
        assert ctx.get_header("missing") is None

    def test_html_tree_cached_and_none_without_body(self) -> None:
        ctx_no_body = DetectionContext(url="https://a.com", status_code=200, headers={}, body_text=None)
        assert ctx_no_body.html_tree is None

        ctx = DetectionContext(url="https://a.com", status_code=200, headers={}, body_text="<p>hi</p>")
        tree1 = ctx.html_tree
        tree2 = ctx.html_tree
        assert tree1 is tree2


class TestHttpLocationPlugin:
    plugin = HttpLocationPlugin()

    def test_301_with_location(self) -> None:
        ctx = DetectionContext(url="https://a.com", status_code=301, headers={"Location": "https://b.com"})
        outcome = self.plugin.detect(ctx)
        assert outcome is not None
        assert outcome.redirect_type == RedirectType.HTTP_301
        assert outcome.destination == "https://b.com"

    def test_all_redirect_codes(self) -> None:
        expected = {
            302: RedirectType.HTTP_302,
            303: RedirectType.HTTP_303,
            307: RedirectType.HTTP_307,
            308: RedirectType.HTTP_308,
        }
        for code, redirect_type in expected.items():
            ctx = DetectionContext(url="https://a.com", status_code=code, headers={"Location": "https://b.com"})
            outcome = self.plugin.detect(ctx)
            assert outcome.redirect_type == redirect_type

    def test_200_no_detection(self) -> None:
        ctx = DetectionContext(url="https://a.com", status_code=200, headers={"Location": "https://b.com"})
        assert self.plugin.detect(ctx) is None

    def test_redirect_status_missing_location_no_crash(self) -> None:
        ctx = DetectionContext(url="https://a.com", status_code=301, headers={})
        assert self.plugin.detect(ctx) is None


class TestMetaRefreshPlugin:
    plugin = MetaRefreshPlugin()

    def _ctx(self, body: str) -> DetectionContext:
        return DetectionContext(url="https://a.com/page", status_code=200, headers={}, body_text=body)

    def test_standard_content_format(self) -> None:
        outcome = self.plugin.detect(
            self._ctx('<meta http-equiv="refresh" content="5;url=https://example.com/target">')
        )
        assert outcome.destination == "https://example.com/target"

    def test_delay_only_is_not_a_redirect(self) -> None:
        assert self.plugin.detect(self._ctx('<meta http-equiv="refresh" content="5">')) is None

    def test_no_body_no_crash(self) -> None:
        ctx = DetectionContext(url="https://a.com", status_code=200, headers={}, body_text=None)
        assert self.plugin.detect(ctx) is None

    def test_first_of_multiple_tags_wins(self) -> None:
        body = (
            '<meta http-equiv="refresh" content="0;url=https://first.com">'
            '<meta http-equiv="refresh" content="0;url=https://second.com">'
        )
        outcome = self.plugin.detect(self._ctx(body))
        assert outcome.destination == "https://first.com"


class TestJavaScriptRedirectPlugin:
    plugin = JavaScriptRedirectPlugin()

    def _ctx(self, script: str) -> DetectionContext:
        return DetectionContext(
            url="https://a.com", status_code=200, headers={}, body_text=f"<script>{script}</script>"
        )

    def test_window_location_assignment(self) -> None:
        outcome = self.plugin.detect(self._ctx('window.location = "https://example.com/a";'))
        assert outcome.destination == "https://example.com/a"

    def test_location_replace(self) -> None:
        outcome = self.plugin.detect(self._ctx("location.replace('https://example.com/b');"))
        assert outcome.destination == "https://example.com/b"

    def test_geolocation_false_positive_guard(self) -> None:
        script = "navigator.geolocation.getCurrentPosition(function(pos){});"
        assert self.plugin.detect(self._ctx(script)) is None

    def test_comparison_not_assignment(self) -> None:
        assert self.plugin.detect(self._ctx('if (location.href == "https://x.com") {}')) is None

    def test_variable_destination_not_extractable(self) -> None:
        assert self.plugin.detect(self._ctx("window.location.href = someVar;")) is None

    def test_external_script_ignored(self) -> None:
        ctx = DetectionContext(
            url="https://a.com",
            status_code=200,
            headers={},
            body_text='<script src="https://cdn.com/a.js">window.location="https://x.com";</script>',
        )
        assert self.plugin.detect(ctx) is None


class TestCloudflarePlugin:
    plugin = CloudflarePlugin()

    def test_not_a_redirect_detector(self) -> None:
        assert not hasattr(self.plugin, "detect")

    def test_classify_via_headers(self) -> None:
        ctx = DetectionContext(url="https://a.com", status_code=200, headers={"CF-RAY": "abc"})
        status = self.plugin.classify(ctx)
        assert status.is_cloudflare is True

    def test_classify_no_signal(self) -> None:
        ctx = DetectionContext(url="https://a.com", status_code=200, headers={}, body_text="<p>hi</p>")
        status = self.plugin.classify(ctx)
        assert status.is_cloudflare is False
