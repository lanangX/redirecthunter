"""Tests for redirecthunter.utils."""

from __future__ import annotations

import pytest

from redirecthunter.utils import (
    MissingTargetError,
    dedupe_preserve_order,
    expand_target,
    extract_domain,
    format_bytes,
    format_latency,
    is_external_domain,
    is_valid_http_url,
    normalize_url,
    parse_set_cookie_headers,
    resolve_relative_url,
    truncate_text,
)


class TestExpandTarget:
    def test_basic_substitution(self) -> None:
        assert (
            expand_target("https://a.com/go?url={TARGET}", "https://example.org")
            == "https://a.com/go?url=https://example.org"
        )

    def test_url_encoded_substitution(self) -> None:
        result = expand_target("https://a.com/go?url={TARGET}", "https://example.org?x=1", url_encode=True)
        assert result == "https://a.com/go?url=https%3A%2F%2Fexample.org%3Fx%3D1"

    def test_no_placeholder_returns_unchanged(self) -> None:
        assert expand_target("https://a.com/x", None) == "https://a.com/x"

    def test_missing_target_raises(self) -> None:
        with pytest.raises(MissingTargetError):
            expand_target("https://a.com/go?url={TARGET}", None)

    def test_empty_target_raises(self) -> None:
        with pytest.raises(MissingTargetError):
            expand_target("https://a.com/go?url={TARGET}", "")


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("EXAMPLE.com/Path", "https://example.com/Path"),
            ("http://Example.com:80/x", "http://example.com/x"),
            ("https://example.com:443/x", "https://example.com/x"),
            ("https://example.com:8443/x", "https://example.com:8443/x"),
        ],
    )
    def test_normalization(self, raw: str, expected: str) -> None:
        assert normalize_url(raw) == expected


class TestResolveRelativeUrl:
    def test_relative_path(self) -> None:
        assert resolve_relative_url("https://a.com/dir/page", "../other") == "https://a.com/other"

    def test_absolute_path(self) -> None:
        assert resolve_relative_url("https://a.com/dir/page", "/abs") == "https://a.com/abs"

    def test_protocol_relative(self) -> None:
        assert resolve_relative_url("https://a.com/dir/page", "//cdn.com/x") == "https://cdn.com/x"

    def test_already_absolute(self) -> None:
        assert resolve_relative_url("https://a.com/x", "https://b.com/y") == "https://b.com/y"


class TestValidation:
    def test_valid_http_url(self) -> None:
        assert is_valid_http_url("https://a.com") is True
        assert is_valid_http_url("http://a.com") is True

    def test_invalid_scheme_rejected(self) -> None:
        assert is_valid_http_url("ftp://a.com") is False

    def test_malformed_rejected(self) -> None:
        assert is_valid_http_url("not a url") is False


class TestExtractDomain:
    def test_lowercased(self) -> None:
        assert extract_domain("https://Sub.Example.com/x") == "sub.example.com"

    def test_unparseable_returns_none(self) -> None:
        assert extract_domain("not a url") is None


class TestIsExternalDomain:
    def test_same_domain_not_external(self) -> None:
        assert is_external_domain("https://example.org/x", "example.org") is False

    def test_subdomain_not_external(self) -> None:
        assert is_external_domain("https://sub.example.org/x", "example.org") is False

    def test_different_domain_is_external(self) -> None:
        assert is_external_domain("https://evil-tracker.com/x", "example.org") is True

    def test_lookalike_domain_not_falsely_matched(self) -> None:
        """Regression guard: naive substring matching would wrongly treat this as
        'belonging to' example.org. Hostname-based comparison must not."""
        assert is_external_domain("https://notexample.org.evil.com/x", "example.org") is True

    def test_www_prefix_on_reference_ignored(self) -> None:
        assert is_external_domain("https://example.org/x", "www.example.org") is False

    def test_unparseable_url_is_not_external(self) -> None:
        assert is_external_domain("not a url", "example.org") is False


class TestDedupe:
    def test_preserves_first_seen_order(self) -> None:
        assert dedupe_preserve_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


class TestCookieParsing:
    def test_parses_multiple_cookies(self) -> None:
        cookies = parse_set_cookie_headers(
            ["session=abc123; Path=/; HttpOnly", "theme=dark; Max-Age=3600"]
        )
        assert cookies == {"session": "abc123", "theme": "dark"}

    def test_malformed_cookie_skipped_not_fatal(self) -> None:
        cookies = parse_set_cookie_headers(["not=avalid=cookie=at=all;;;", "ok=1"])
        assert cookies.get("ok") == "1"


class TestFormatting:
    def test_truncate_short_text_unchanged(self) -> None:
        assert truncate_text("short", 10) == "short"

    def test_truncate_long_text(self) -> None:
        result = truncate_text("x" * 300, 10)
        assert len(result) == 10
        assert result.endswith("\u2026")

    def test_format_bytes(self) -> None:
        assert format_bytes(None) == "-"
        assert format_bytes(500) == "500 B"
        assert format_bytes(2048) == "2.0 KB"

    def test_format_latency(self) -> None:
        assert format_latency(450) == "450 ms"
        assert format_latency(1500) == "1.50 s"
