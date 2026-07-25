"""Tests for redirecthunter.engine.

Uses respx to mock the HTTP transport layer so these tests run fully
offline and deterministically, rather than depending on a live server
(which was used for manual integration testing during development but is
not suitable for an automated, CI-friendly test suite).
"""

from __future__ import annotations

import time

import httpx
import respx

from redirecthunter.engine import Engine, RateLimiter
from redirecthunter.models import CandidateURL, HTTPMethod, RedirectType


class TestRateLimiter:
    """Pure unit tests, no HTTP involved -- just timing behavior."""

    async def test_paces_requests_to_configured_rate(self) -> None:
        limiter = RateLimiter(5.0)  # 5/sec -> ~0.2s between acquisitions
        start = time.monotonic()
        for _ in range(3):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.35  # at least 2 full intervals between 3 acquisitions

    async def test_unlimited_is_effectively_instant(self) -> None:
        limiter = RateLimiter(None)
        start = time.monotonic()
        for _ in range(200):
            await limiter.acquire()
        assert time.monotonic() - start < 0.05


class TestEngine:
    async def test_direct_200_no_redirect(self, sample_config) -> None:
        config = sample_config.model_copy(update={"method": HTTPMethod.GET, "target": None})
        engine = Engine(config)

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/direct").mock(
                return_value=httpx.Response(200, headers={"Server": "nginx"}, text="<p>hi</p>")
            )
            results = []

            async def on_result(r, h):
                results.append((r, h))

            await engine.run([CandidateURL(raw_url="https://example.test/direct")], on_result, total=1)

        result, headers = results[0]
        assert result.alive is True
        assert result.status_code == 200
        assert result.redirect_type == RedirectType.NONE
        assert headers is not None
        assert headers.get("server") == "nginx"

    async def test_full_redirect_chain(self, sample_config) -> None:
        config = sample_config.model_copy(update={"method": HTTPMethod.GET, "target": None, "max_redirects": 5})
        engine = Engine(config)

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/start").mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.test/mid", "Server": "nginx"})
            )
            mock.get("https://example.test/mid").mock(
                return_value=httpx.Response(
                    200, headers={"Server": "cloudflare", "CF-RAY": "ray1", "Content-Type": "text/html"}, text="<p>done</p>"
                )
            )
            results = []

            async def on_result(r, h):
                results.append((r, h))

            await engine.run([CandidateURL(raw_url="https://example.test/start")], on_result, total=1)

        result, headers = results[0]
        assert result.alive is True
        assert result.hop_count == 1
        assert result.status_code == 301
        assert result.redirect_type == RedirectType.HTTP_301
        assert result.final_url == "https://example.test/mid"
        assert result.server == "cloudflare"
        assert result.fingerprint.cloudflare.is_cloudflare is True
        assert headers.get("cf-ray") == "ray1"

    async def test_max_redirects_cap_enforced(self, sample_config) -> None:
        config = sample_config.model_copy(update={"method": HTTPMethod.GET, "target": None, "max_redirects": 1})
        engine = Engine(config)

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/a").mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.test/b"})
            )
            mock.get("https://example.test/b").mock(
                return_value=httpx.Response(302, headers={"Location": "https://example.test/c"})
            )
            results = []

            async def on_result(r, h):
                results.append((r, h))

            await engine.run([CandidateURL(raw_url="https://example.test/a")], on_result, total=1)

        result, _ = results[0]
        # max_redirects=1: hop 0 (301) is followed, hop 1 (302 at /b) becomes terminal
        # even though it is itself a redirect -- the chain does not continue to /c.
        assert result.hop_count == 1
        assert result.final_url == "https://example.test/b"

    async def test_connection_error_marks_dead(self, sample_config) -> None:
        config = sample_config.model_copy(update={"method": HTTPMethod.GET, "target": None, "retry": 0})
        engine = Engine(config)

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/dead").mock(side_effect=httpx.ConnectError("boom"))
            results = []

            async def on_result(r, h):
                results.append((r, h))

            await engine.run([CandidateURL(raw_url="https://example.test/dead")], on_result, total=1)

        result, headers = results[0]
        assert result.alive is False
        assert result.error is not None
        assert headers is None

    async def test_retry_recovers_from_transient_failure(self, sample_config) -> None:
        config = sample_config.model_copy(
            update={"method": HTTPMethod.GET, "target": None, "retry": 3, "retry_backoff": 0.01}
        )
        engine = Engine(config)
        call_count = {"n": 0}

        def flaky(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise httpx.ConnectError("transient", request=request)
            return httpx.Response(200, text="<p>recovered</p>")

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/flaky").mock(side_effect=flaky)
            results = []

            async def on_result(r, h):
                results.append((r, h))

            await engine.run([CandidateURL(raw_url="https://example.test/flaky")], on_result, total=1)

        result, _ = results[0]
        assert result.alive is True
        assert result.error is None
        assert call_count["n"] == 3

    async def test_missing_target_does_not_abort_batch(self, sample_config) -> None:
        config = sample_config.model_copy(update={"method": HTTPMethod.GET, "target": None})
        engine = Engine(config)

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/ok").mock(return_value=httpx.Response(200, text="<p>ok</p>"))
            results = []

            async def on_result(r, h):
                results.append((r, h))

            candidates = [
                CandidateURL(raw_url="https://example.test/go?url={TARGET}"),  # needs target, none configured
                CandidateURL(raw_url="https://example.test/ok"),  # no placeholder, should still process fine
            ]
            stats = await engine.run(candidates, on_result, total=2)

        assert stats.processed == 2
        by_url = {r.source_url: r for r, _ in results}
        assert by_url["https://example.test/go?url={TARGET}"].alive is False
        assert by_url["https://example.test/go?url={TARGET}"].error is not None
        assert by_url["https://example.test/ok"].alive is True

    async def test_meta_refresh_and_js_redirect_detected(self, sample_config) -> None:
        config = sample_config.model_copy(update={"method": HTTPMethod.GET, "target": None, "max_redirects": 3})
        engine = Engine(config)

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/meta").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<meta http-equiv="refresh" content="0;url=/meta-target">',
                )
            )
            mock.get("https://example.test/meta-target").mock(return_value=httpx.Response(200, text="<p>landed</p>"))
            mock.get("https://example.test/js").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<script>window.location.href="/js-target";</script>',
                )
            )
            mock.get("https://example.test/js-target").mock(return_value=httpx.Response(200, text="<p>landed</p>"))

            results = []

            async def on_result(r, h):
                results.append((r, h))

            candidates = [
                CandidateURL(raw_url="https://example.test/meta"),
                CandidateURL(raw_url="https://example.test/js"),
            ]
            await engine.run(candidates, on_result, total=2)

        by_url = {r.source_url: r for r, _ in results}
        meta_result = by_url["https://example.test/meta"]
        assert meta_result.redirect_type == RedirectType.META_REFRESH
        assert meta_result.final_url == "https://example.test/meta-target"

        js_result = by_url["https://example.test/js"]
        assert js_result.redirect_type == RedirectType.JAVASCRIPT
        assert js_result.final_url == "https://example.test/js-target"
