"""Tests for redirecthunter.fingerprint."""

from __future__ import annotations

from redirecthunter.fingerprint import build_fingerprint, detect_cloudflare, detect_software


class TestDetectSoftware:
    def test_nginx(self) -> None:
        assert detect_software({"Server": "nginx/1.24.0"}) == "nginx"

    def test_apache(self) -> None:
        assert detect_software({"Server": "Apache/2.4.58 (Ubuntu)"}) == "Apache"

    def test_litespeed(self) -> None:
        assert detect_software({"Server": "LiteSpeed"}) == "LiteSpeed"

    def test_iis(self) -> None:
        assert detect_software({"Server": "Microsoft-IIS/10.0"}) == "IIS"

    def test_cloudflare_via_server_header(self) -> None:
        assert detect_software({"Server": "cloudflare"}) == "Cloudflare"

    def test_cloudflare_via_cf_ray_header_only(self) -> None:
        assert detect_software({"CF-RAY": "8a1b2c3d4e5f6789-SIN"}) == "Cloudflare"

    def test_cloudfront(self) -> None:
        assert detect_software({"Server": "CloudFront", "X-Amz-Cf-Id": "abc123"}) == "CloudFront"

    def test_fastly_takes_precedence_over_underlying_varnish(self) -> None:
        headers = {"Server": "Varnish", "X-Served-By": "cache-sin", "X-Fastly-Request-ID": "xyz"}
        assert detect_software(headers) == "Fastly"

    def test_plain_varnish(self) -> None:
        assert detect_software({"Via": "1.1 varnish"}) == "Varnish"

    def test_akamai(self) -> None:
        assert detect_software({"Server": "AkamaiGHost"}) == "Akamai"

    def test_no_signal_returns_none(self) -> None:
        assert detect_software({}) is None


class TestDetectCloudflare:
    def test_cf_ray_header(self) -> None:
        status = detect_cloudflare({"CF-RAY": "8a1b2c3d4e5f6789-SIN"})
        assert status.is_cloudflare is True
        assert status.has_cf_ray is True
        assert status.cf_ray_id == "8a1b2c3d4e5f6789-SIN"

    def test_cf_clearance_cookie(self) -> None:
        status = detect_cloudflare({}, cookies={"cf_clearance": "abc"})
        assert status.is_cloudflare is True
        assert status.has_cf_clearance_cookie is True

    def test_challenge_page_detected(self) -> None:
        body = '<html><title>Just a moment...</title><div class=cf_chl_opt></div></html>'
        status = detect_cloudflare({"Server": "cloudflare"}, body_sample=body)
        assert status.is_challenge_page is True

    def test_cdn_cgi_path_marker(self) -> None:
        body = '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate"></script>'
        status = detect_cloudflare({}, body_sample=body)
        assert status.has_cdn_cgi_path is True
        assert status.is_cloudflare is True

    def test_no_signal(self) -> None:
        status = detect_cloudflare({})
        assert status.is_cloudflare is False
        assert status.is_challenge_page is False


class TestBuildFingerprint:
    def test_combines_software_and_cloudflare(self) -> None:
        fp = build_fingerprint({"Server": "cloudflare", "CF-RAY": "ray1"})
        assert fp.detected_software == "Cloudflare"
        assert fp.cloudflare.is_cloudflare is True
        assert fp.server_header == "cloudflare"

    def test_head_request_no_body_still_works(self) -> None:
        fp = build_fingerprint({"Server": "nginx"}, body_sample=None)
        assert fp.detected_software == "nginx"
        assert fp.cloudflare.is_cloudflare is False
