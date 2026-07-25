"""Tests for redirecthunter.analyzer."""

from __future__ import annotations

from redirecthunter.analyzer import ResponseAnalyzer
from redirecthunter.models import HTTPMethod, RedirectType


class TestResponseAnalyzer:
    def test_full_chain_semantics(self) -> None:
        """Verifies the documented redirect_chain / status_code / final_url semantics."""
        analyzer = ResponseAnalyzer()
        chain = []

        h0 = analyzer.analyze_hop(
            url="https://a.com/go", status_code=301,
            headers={"Location": "https://mid.com/step2", "Server": "nginx"},
            body_text=None, hop_index=0, latency_ms=40.0,
        )
        chain.append(h0.hop)

        h1 = analyzer.analyze_hop(
            url=h0.next_url, status_code=200,
            headers={"Server": "cloudflare", "CF-RAY": "abc", "Content-Type": "text/html", "Content-Length": "10"},
            body_text="<p>hi</p>", hop_index=1, latency_ms=30.0,
            set_cookie_values=["session=xyz"],
        )

        result = analyzer.build_result(
            scan_id="s1", source_url="https://a.com/go", expanded_url="https://a.com/go",
            http_method=HTTPMethod.HEAD, redirect_chain=chain, terminal_hop_analysis=h1,
            total_latency_ms=70.0, alive=True,
        )

        assert result.hop_count == 1  # only the redirect is counted, not the terminal hop
        assert result.status_code == 301  # first hop
        assert result.redirect_type == RedirectType.HTTP_301
        assert result.final_url == "https://mid.com/step2"  # terminal response url
        assert result.server == "cloudflare"  # terminal response server
        assert result.cookies == {"session": "xyz"}
        assert result.fingerprint.cloudflare.is_cloudflare is True

    def test_direct_response_no_redirect(self) -> None:
        analyzer = ResponseAnalyzer()
        h0 = analyzer.analyze_hop(
            url="https://a.com", status_code=200, headers={"Server": "Apache"},
            body_text="<p>hi</p>", hop_index=0, latency_ms=50.0,
        )
        assert h0.next_url is None
        result = analyzer.build_result(
            scan_id="s1", source_url="https://a.com", expanded_url="https://a.com",
            http_method=HTTPMethod.HEAD, redirect_chain=[], terminal_hop_analysis=h0,
            total_latency_ms=50.0, alive=True,
        )
        assert result.hop_count == 0
        assert result.status_code == 200
        assert result.redirect_type == RedirectType.NONE
        assert result.final_url == "https://a.com"

    def test_connection_error_no_response(self) -> None:
        analyzer = ResponseAnalyzer()
        result = analyzer.build_result(
            scan_id="s1", source_url="https://dead.com", expanded_url="https://dead.com",
            http_method=HTTPMethod.HEAD, redirect_chain=[], terminal_hop_analysis=None,
            total_latency_ms=5000.0, alive=False, error="ConnectTimeout",
        )
        assert result.alive is False
        assert result.status_code is None
        assert result.final_url is None
        assert result.error == "ConnectTimeout"

    def test_follow_redirects_false_still_classifies_first_response(self) -> None:
        analyzer = ResponseAnalyzer()
        h0 = analyzer.analyze_hop(
            url="https://e.com", status_code=302, headers={"Location": "https://example.org/"},
            body_text=None, hop_index=0, latency_ms=20.0,
        )
        result = analyzer.build_result(
            scan_id="s1", source_url="https://e.com", expanded_url="https://e.com",
            http_method=HTTPMethod.HEAD, redirect_chain=[],  # engine chose not to follow
            terminal_hop_analysis=h0, total_latency_ms=20.0, alive=True,
        )
        assert result.hop_count == 0
        assert result.status_code == 302
        assert result.redirect_type == RedirectType.HTTP_302
        assert result.final_url == "https://e.com"

    def test_relative_location_resolved_against_hop_url(self) -> None:
        analyzer = ResponseAnalyzer()
        h0 = analyzer.analyze_hop(
            url="https://a.com/dir/page", status_code=302, headers={"Location": "../other"},
            body_text=None, hop_index=0, latency_ms=10.0,
        )
        assert h0.next_url == "https://a.com/other"
