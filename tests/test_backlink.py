"""Tests for redirecthunter/backlink.py — the shared matching module.

Ported from tests/test_backlink_checker.py's coverage of the matching
helpers, now exercising them directly at their new home. See MEMORY.md /
.scratch/backlink-check-cli/spec.md for why this extraction happened.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from redirecthunter.backlink import (
    BacklinkResult,
    PlaywrightNotInstalledError,
    build_text_mention_pattern,
    check_one,
    check_one_browser,
    extract_hostname,
    hostname_matches,
    looks_like_bot_block_status,
    looks_like_challenge_page,
    looks_like_login_wall,
    normalize_domain,
    normalize_hostname,
    resolve_account_headers,
    run_backlink_checks,
    run_backlink_checks_browser,
)


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("medilana.id", "medilana.id"),
        ("https://medilana.id", "medilana.id"),
        ("http://www.medilana.id/", "medilana.id"),
        ("MEDILANA.ID.", "medilana.id"),
        ("medilana.id/artikel", "medilana.id"),
    ],
)
def test_normalize_domain(domain: str, expected: str) -> None:
    assert normalize_domain(domain) == expected


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("MEDILANA.ID", "medilana.id"),
        ("www.medilana.id", "medilana.id"),
        ("medilana.id.", "medilana.id"),
    ],
)
def test_normalize_hostname(hostname: str, expected: str) -> None:
    assert normalize_hostname(hostname) == expected


class TestHostnameMatches:
    def test_exact_match(self) -> None:
        assert hostname_matches("medilana.id", "medilana.id", allow_subdomains=True)
        assert hostname_matches("medilana.id", "medilana.id", allow_subdomains=False)

    def test_subdomain_match_only_when_allowed(self) -> None:
        assert hostname_matches("blog.medilana.id", "medilana.id", allow_subdomains=True)
        assert not hostname_matches("blog.medilana.id", "medilana.id", allow_subdomains=False)

    def test_never_matches_as_substring(self) -> None:
        assert not hostname_matches("notmedilana.id", "medilana.id", allow_subdomains=True)
        assert not hostname_matches("medilana.id.evil.com", "medilana.id", allow_subdomains=True)
        assert not hostname_matches("somemedilana.id", "medilana.id", allow_subdomains=True)


class TestExtractHostname:
    def test_absolute_href(self) -> None:
        assert extract_hostname("https://medilana.id/article", "https://source.test/x") == "medilana.id"

    def test_protocol_relative_href(self) -> None:
        assert extract_hostname("//medilana.id/article", "https://source.test/x") == "medilana.id"

    def test_relative_href_resolves_against_base(self) -> None:
        assert extract_hostname("/about", "https://source.test/x") == "source.test"

    def test_bare_scheme_less_domain_promoted_to_host(self) -> None:
        assert extract_hostname("medilana.id/artikel", "https://source.test/x") == "medilana.id"

    def test_normal_relative_path_not_misread_as_host(self) -> None:
        assert extract_hostname("about", "https://source.test/x") == "source.test"

    def test_empty_href_returns_none(self) -> None:
        assert extract_hostname("", "https://source.test/x") is None


class TestBuildTextMentionPattern:
    def test_matches_bare_domain_mention(self) -> None:
        pattern = build_text_mention_pattern("medilana.id")
        assert pattern.search("Visit medilana.id for more info")

    def test_does_not_match_as_substring_of_longer_host(self) -> None:
        pattern = build_text_mention_pattern("medilana.id")
        assert not pattern.search("notmedilana.id has nothing to do with us")
        assert not pattern.search("see medilana.id.evil.com instead")


class TestLooksLikeBotBlockStatus:
    def test_999_is_bot_block(self) -> None:
        assert looks_like_bot_block_status(999)

    def test_ordinary_statuses_are_not(self) -> None:
        assert not looks_like_bot_block_status(200)
        assert not looks_like_bot_block_status(404)


class TestLooksLikeLoginWall:
    def test_login_path_with_next_query_is_wall(self) -> None:
        assert looks_like_login_wall("https://facebook.com/login/?next=/somepage")

    def test_login_path_without_carry_forward_query_is_not_wall(self) -> None:
        assert not looks_like_login_wall("https://example.test/login")

    def test_non_login_path_is_not_wall(self) -> None:
        assert not looks_like_login_wall("https://example.test/article?next=1")


class TestLooksLikeChallengePage:
    def test_cloudflare_title_marker(self) -> None:
        assert looks_like_challenge_page(503, "Just a moment...", httpx.Headers({}))

    def test_cf_mitigated_header(self) -> None:
        assert looks_like_challenge_page(200, "Anything", httpx.Headers({"cf-mitigated": "challenge"}))

    def test_ordinary_page_is_not_challenge(self) -> None:
        assert not looks_like_challenge_page(200, "My Blog Post", httpx.Headers({}))


_HTML_DIRECT_ANCHOR = """<html><head><title>Post</title></head><body>
<a href="https://medilana.id/article" rel="nofollow sponsored" target="_blank">a link</a>
</body></html>"""

_HTML_INDIRECT = """<html><head><title>Post</title></head><body>
<a href="https://redirector.test/go?to=medilana.id/article" rel="ugc" target="_self">tracker link</a>
</body></html>"""

_HTML_TEXT_ONLY = """<html><head><title>Post</title></head><body>
<p>We love medilana.id but forgot to link it.</p>
</body></html>"""

_HTML_NO_MENTION = """<html><head><title>Post</title></head><body>
<p>Nothing relevant here.</p>
</body></html>"""


class TestCheckOne:
    async def test_direct_anchor_is_confirmed(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HTML_DIRECT_ANCHOR)
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert isinstance(result, BacklinkResult)
        assert result.match_found is True
        assert result.match_type == "anchor"
        assert result.matched_href == "https://medilana.id/article"

    async def test_indirect_match_when_enabled(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HTML_INDIRECT)
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.match_type == "indirect_query"

    async def test_indirect_disabled_falls_through_to_text_mention(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HTML_INDIRECT)
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=False,
                )

        assert result.match_found is False
        assert result.match_type in ("not_found", "text_mention_only")

    async def test_text_mention_only(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HTML_TEXT_ONLY)
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.match_found is False
        assert result.match_type == "text_mention_only"
        assert result.text_mentions == 1

    async def test_not_found(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HTML_NO_MENTION)
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.match_found is False
        assert result.match_type == "not_found"

    async def test_network_error_is_captured(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(side_effect=httpx.ConnectError("boom"))
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.error is not None
        assert result.match_found is False

    async def test_bot_block_status(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(return_value=httpx.Response(999))
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.blocked is True

    async def test_login_wall_short_circuits_page_scan(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(302, headers={"Location": "https://source.test/login?next=/page"})
            )
            mock.get("https://source.test/login", params={"next": "/page"}).mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text="<html><body>Please log in</body></html>",
                )
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.requires_login is True
        assert result.match_found is False

    async def test_final_url_is_target_takes_priority(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://short.test/x").mock(
                return_value=httpx.Response(302, headers={"Location": "https://medilana.id/"})
            )
            mock.get("https://medilana.id/").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>hi</body></html>")
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://short.test/x", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.match_type == "final_url_is_target"
        assert result.match_found is True


class TestResolveAccountHeaders:
    def test_url_with_registered_account(self) -> None:
        headers = resolve_account_headers(
            "https://a.com",
            {"https://a.com": "account_001"},
            {"account_001": {"Cookie": "session=xyz"}},
        )
        assert headers == {"Cookie": "session=xyz"}

    def test_url_with_no_account_returns_none(self) -> None:
        headers = resolve_account_headers(
            "https://public.com",
            {"https://a.com": "account_001"},
            {"account_001": {"Cookie": "session=xyz"}},
        )
        assert headers is None

    def test_registered_account_with_empty_headers_returns_none(self) -> None:
        headers = resolve_account_headers(
            "https://a.com",
            {"https://a.com": "account_001"},
            {"account_001": {}},
        )
        assert headers is None

    def test_no_per_url_account_map_returns_none(self) -> None:
        assert resolve_account_headers("https://a.com", None, {"account_001": {"Cookie": "x"}}) is None

    def test_no_account_headers_registry_returns_none(self) -> None:
        assert resolve_account_headers("https://a.com", {"https://a.com": "account_001"}, None) is None


class TestRunBacklinkChecksAccountIsolation:
    """Account headers must never leak across concurrent requests for other accounts."""

    async def test_concurrent_requests_never_cross_account_headers(self) -> None:
        captured: dict[str, httpx.Request] = {}

        def _capture(name: str):
            def _responder(request: httpx.Request) -> httpx.Response:
                captured[name] = request
                return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html></html>")

            return _responder

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://a.example.com/1").mock(side_effect=_capture("a1"))
            mock.get("https://a.example.com/2").mock(side_effect=_capture("a2"))
            mock.get("https://b.example.com/1").mock(side_effect=_capture("b1"))
            mock.get("https://b.example.com/2").mock(side_effect=_capture("b2"))

            results: list[BacklinkResult] = []

            async def on_result(result: BacklinkResult) -> None:
                results.append(result)

            await run_backlink_checks(
                [
                    "https://a.example.com/1",
                    "https://a.example.com/2",
                    "https://b.example.com/1",
                    "https://b.example.com/2",
                ],
                frozenset({"medilana.id"}),
                concurrency=4,
                timeout=5.0,
                allow_subdomains=True,
                check_indirect=False,
                user_agent="test-agent",
                per_url_account_id={
                    "https://a.example.com/1": "account_a",
                    "https://a.example.com/2": "account_a",
                    "https://b.example.com/1": "account_b",
                    "https://b.example.com/2": "account_b",
                },
                account_headers={
                    "account_a": {"Cookie": "session=account-a-secret"},
                    "account_b": {"Cookie": "session=account-b-secret"},
                },
                on_result=on_result,
            )

        assert len(results) == 4
        assert captured["a1"].headers["Cookie"] == "session=account-a-secret"
        assert captured["a2"].headers["Cookie"] == "session=account-a-secret"
        assert captured["b1"].headers["Cookie"] == "session=account-b-secret"
        assert captured["b2"].headers["Cookie"] == "session=account-b-secret"
        # Never crossed: account A's cookie must never appear on a B request, and vice versa.
        assert captured["a1"].headers["Cookie"] != captured["b1"].headers["Cookie"]
        assert captured["a2"].headers["Cookie"] != captured["b2"].headers["Cookie"]

    async def test_public_url_alongside_account_urls_gets_no_account_cookie(self) -> None:
        captured: dict[str, httpx.Request] = {}

        def _capture(name: str):
            def _responder(request: httpx.Request) -> httpx.Response:
                captured[name] = request
                return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html></html>")

            return _responder

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://a.example.com/private").mock(side_effect=_capture("private"))
            mock.get("https://public.example.com/page").mock(side_effect=_capture("public"))

            results: list[BacklinkResult] = []

            async def on_result(result: BacklinkResult) -> None:
                results.append(result)

            await run_backlink_checks(
                ["https://a.example.com/private", "https://public.example.com/page"],
                frozenset({"medilana.id"}),
                concurrency=2,
                timeout=5.0,
                allow_subdomains=True,
                check_indirect=False,
                user_agent="test-agent",
                per_url_account_id={"https://a.example.com/private": "account_a"},
                account_headers={"account_a": {"Cookie": "session=account-a-secret"}},
                on_result=on_result,
            )

        assert captured["private"].headers["Cookie"] == "session=account-a-secret"
        assert "Cookie" not in captured["public"].headers


# --------------------------------------------------------------------------
# Database persistence tests (ticket 02) -- style: tests/test_crawl_database.py
# --------------------------------------------------------------------------

from pathlib import Path as _Path  # noqa: E402

from redirecthunter.database import Database, DatabaseError  # noqa: E402
from redirecthunter.models import BacklinkCheckConfig, RunStatus  # noqa: E402


@pytest.fixture
async def db(tmp_path: _Path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def sample_backlink_config(tmp_path: _Path) -> BacklinkCheckConfig:
    return BacklinkCheckConfig(
        domain="medilana.id",
        input_path=tmp_path / "urls.txt",
        label="test run",
    )


class TestBacklinkCheckPersistence:
    async def test_create_and_fetch_config(self, db: Database, sample_backlink_config: BacklinkCheckConfig) -> None:
        await db.create_backlink_check(sample_backlink_config, total_urls=3)
        assert await db.backlink_check_exists(sample_backlink_config.backlink_id) is True
        assert await db.backlink_check_exists("nonexistent") is False

        fetched = await db.get_backlink_check_config(sample_backlink_config.backlink_id)
        assert fetched is not None
        assert fetched.domain == "medilana.id"
        assert fetched.label == "test run"

    async def test_update_status(self, db: Database, sample_backlink_config: BacklinkCheckConfig) -> None:
        await db.create_backlink_check(sample_backlink_config, total_urls=1)
        await db.update_backlink_check_status(
            sample_backlink_config.backlink_id, RunStatus.COMPLETED, finished=True
        )

        summary = await db.get_backlink_check_summary(sample_backlink_config.backlink_id)
        assert summary is not None
        assert summary.status is RunStatus.COMPLETED
        assert summary.finished_at is not None

    async def test_save_and_stream_results(self, db: Database, sample_backlink_config: BacklinkCheckConfig) -> None:
        await db.create_backlink_check(sample_backlink_config, total_urls=2)

        confirmed = BacklinkResult(
            source_url="https://source.test/a",
            final_url="https://source.test/a",
            status_code=200,
            match_found=True,
            match_type="anchor",
            matched_href="https://medilana.id/x",
            rel="nofollow",
            target="_blank",
            matched_target="medilana.id",
        )
        not_found = BacklinkResult(source_url="https://source.test/b", status_code=200)

        await db.save_backlink_result(sample_backlink_config.backlink_id, confirmed)
        await db.save_backlink_result(sample_backlink_config.backlink_id, not_found)

        results = [r async for r in db.iter_backlink_results(sample_backlink_config.backlink_id)]
        assert len(results) == 2
        by_url = {r.source_url: r for r in results}
        assert by_url["https://source.test/a"].match_found is True
        assert by_url["https://source.test/a"].matched_href == "https://medilana.id/x"
        assert by_url["https://source.test/a"].matched_target == "medilana.id"
        assert by_url["https://source.test/b"].match_found is False
        assert by_url["https://source.test/b"].matched_target is None

    async def test_multi_target_match_persists_which_member_matched(
        self, db: Database, sample_backlink_config: BacklinkCheckConfig
    ) -> None:
        """`matched_target` records which member of a multi-target `frozenset`
        actually matched (e.g. a `bl-chain` middle tier or a `;`-separated
        per-row override), not just that *some* target matched."""
        await db.create_backlink_check(sample_backlink_config, total_urls=1)
        result = BacklinkResult(
            source_url="https://source.test/multi",
            match_found=True,
            match_type="anchor",
            matched_href="https://form.medilana.com/book",
            target=None,
            matched_target="form.medilana.com",
        )

        await db.save_backlink_result(sample_backlink_config.backlink_id, result)

        [reloaded] = [r async for r in db.iter_backlink_results(sample_backlink_config.backlink_id)]
        assert reloaded.matched_target == "form.medilana.com"

    async def test_confirmed_only_filter(self, db: Database, sample_backlink_config: BacklinkCheckConfig) -> None:
        await db.create_backlink_check(sample_backlink_config, total_urls=2)
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://source.test/a", match_found=True, match_type="anchor"),
        )
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://source.test/b", match_found=False),
        )

        confirmed = [
            r async for r in db.iter_backlink_results(sample_backlink_config.backlink_id, confirmed_only=True)
        ]
        assert len(confirmed) == 1
        assert confirmed[0].source_url == "https://source.test/a"

    async def test_match_type_filter(self, db: Database, sample_backlink_config: BacklinkCheckConfig) -> None:
        await db.create_backlink_check(sample_backlink_config, total_urls=2)
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://source.test/a", match_type="text_mention_only", text_mentions=1),
        )
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://source.test/b", match_type="not_found"),
        )

        filtered = [
            r
            async for r in db.iter_backlink_results(
                sample_backlink_config.backlink_id, match_type="text_mention_only"
            )
        ]
        assert len(filtered) == 1
        assert filtered[0].source_url == "https://source.test/a"

    async def test_get_summary_counts(self, db: Database, sample_backlink_config: BacklinkCheckConfig) -> None:
        await db.create_backlink_check(sample_backlink_config, total_urls=5)
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://s.test/1", match_found=True, match_type="anchor"),
        )
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://s.test/2", match_found=True, match_type="indirect_query"),
        )
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://s.test/3", match_type="text_mention_only", text_mentions=2),
        )
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://s.test/4", blocked=True),
        )
        await db.save_backlink_result(
            sample_backlink_config.backlink_id,
            BacklinkResult(source_url="https://s.test/5", error="ConnectError: boom"),
        )

        summary = await db.get_backlink_check_summary(sample_backlink_config.backlink_id)
        assert summary is not None
        assert summary.total_urls == 5
        assert summary.confirmed == 1
        assert summary.indirect == 1
        assert summary.text_mention_only == 1
        assert summary.blocked == 1
        assert summary.error == 1

    async def test_list_backlink_checks(self, db: Database, tmp_path: _Path) -> None:
        cfg1 = BacklinkCheckConfig(domain="a.test", input_path=tmp_path / "a.txt")
        cfg2 = BacklinkCheckConfig(domain="b.test", input_path=tmp_path / "b.txt")
        await db.create_backlink_check(cfg1, total_urls=0)
        await db.create_backlink_check(cfg2, total_urls=0)

        summaries = await db.list_backlink_checks()
        assert len(summaries) == 2
        domains = {s.domain for s in summaries}
        assert domains == {"a.test", "b.test"}

    async def test_resolve_backlink_check_id_by_prefix(
        self, db: Database, sample_backlink_config: BacklinkCheckConfig
    ) -> None:
        await db.create_backlink_check(sample_backlink_config, total_urls=0)
        prefix = sample_backlink_config.backlink_id[:8]
        resolved = await db.resolve_backlink_check_id(prefix)
        assert resolved == sample_backlink_config.backlink_id

    async def test_resolve_backlink_check_id_not_found(self, db: Database) -> None:
        with pytest.raises(DatabaseError):
            await db.resolve_backlink_check_id("nonexistent")

    async def test_delete_backlink_check_cascades(
        self, db: Database, sample_backlink_config: BacklinkCheckConfig
    ) -> None:
        await db.create_backlink_check(sample_backlink_config, total_urls=1)
        await db.save_backlink_result(
            sample_backlink_config.backlink_id, BacklinkResult(source_url="https://s.test/1")
        )

        deleted_count = await db.delete_backlink_check(sample_backlink_config.backlink_id)
        assert deleted_count == 1
        assert await db.backlink_check_exists(sample_backlink_config.backlink_id) is False


# --------------------------------------------------------------------------
# Database persistence tests (ticket 03, bl-chain) -- style matches
# TestBacklinkCheckPersistence above. Each chain tier is still an
# ordinary backlink_checks/backlink_results run (created the same way
# TestBacklinkCheckPersistence exercises above); these tests cover only
# the chain-level record and the tier-linking/ordering on top of it.
# --------------------------------------------------------------------------

from redirecthunter.models import BacklinkChainConfig  # noqa: E402


@pytest.fixture
def sample_chain_config(tmp_path: _Path) -> BacklinkChainConfig:
    return BacklinkChainConfig(
        domain="medilana.id",
        tier_paths=[tmp_path / "tier1.txt", tmp_path / "tier2.txt"],
        label="test chain",
    )


class TestBacklinkChainPersistence:
    async def test_create_and_fetch_chain(
        self, db: Database, sample_chain_config: BacklinkChainConfig
    ) -> None:
        await db.create_backlink_chain(sample_chain_config)

        summary = await db.get_backlink_chain_summary(sample_chain_config.chain_id)
        assert summary is not None
        assert summary.chain_id == sample_chain_config.chain_id
        assert summary.domain == "medilana.id"
        assert summary.label == "test chain"
        assert summary.status is RunStatus.RUNNING
        assert summary.tiers == []

    async def test_get_chain_summary_missing_returns_none(self, db: Database) -> None:
        assert await db.get_backlink_chain_summary("nonexistent") is None

    async def test_update_chain_status(
        self, db: Database, sample_chain_config: BacklinkChainConfig
    ) -> None:
        await db.create_backlink_chain(sample_chain_config)
        await db.update_backlink_chain_status(
            sample_chain_config.chain_id, RunStatus.COMPLETED, finished=True
        )

        summary = await db.get_backlink_chain_summary(sample_chain_config.chain_id)
        assert summary is not None
        assert summary.status is RunStatus.COMPLETED

    async def test_link_chain_tier_builds_ordered_tier_summaries(
        self, db: Database, sample_chain_config: BacklinkChainConfig, tmp_path: _Path
    ) -> None:
        await db.create_backlink_chain(sample_chain_config)

        tier1_config = BacklinkCheckConfig(domain="medilana.id", input_path=tmp_path / "tier1.txt")
        tier2_config = BacklinkCheckConfig(domain="tier1host.test", input_path=tmp_path / "tier2.txt")
        await db.create_backlink_check(tier1_config, total_urls=1)
        await db.create_backlink_check(tier2_config, total_urls=1)
        await db.save_backlink_result(
            tier1_config.backlink_id,
            BacklinkResult(source_url="https://tier1host.test/a", match_found=True, match_type="anchor"),
        )
        await db.save_backlink_result(
            tier2_config.backlink_id,
            BacklinkResult(source_url="https://tier2host.test/a", match_found=False),
        )

        # Linked out of numeric order to prove get_backlink_chain_summary
        # sorts by tier_index, not insertion order.
        await db.link_chain_tier(
            sample_chain_config.chain_id, 1, tier2_config.backlink_id, tmp_path / "tier2.txt"
        )
        await db.link_chain_tier(
            sample_chain_config.chain_id, 0, tier1_config.backlink_id, tmp_path / "tier1.txt"
        )

        summary = await db.get_backlink_chain_summary(sample_chain_config.chain_id)
        assert summary is not None
        assert [t.backlink_id for t in summary.tiers] == [
            tier1_config.backlink_id,
            tier2_config.backlink_id,
        ]
        assert summary.tiers[0].confirmed == 1
        assert summary.tiers[1].confirmed == 0

    async def test_list_backlink_chains(self, db: Database, tmp_path: _Path) -> None:
        cfg1 = BacklinkChainConfig(domain="a.test", tier_paths=[tmp_path / "a1.txt", tmp_path / "a2.txt"])
        cfg2 = BacklinkChainConfig(domain="b.test", tier_paths=[tmp_path / "b1.txt", tmp_path / "b2.txt"])
        await db.create_backlink_chain(cfg1)
        await db.create_backlink_chain(cfg2)

        summaries = await db.list_backlink_chains()
        assert len(summaries) == 2
        domains = {s.domain for s in summaries}
        assert domains == {"a.test", "b.test"}

    async def test_resolve_backlink_chain_id_by_prefix(
        self, db: Database, sample_chain_config: BacklinkChainConfig
    ) -> None:
        await db.create_backlink_chain(sample_chain_config)
        prefix = sample_chain_config.chain_id[:8]
        resolved = await db.resolve_backlink_chain_id(prefix)
        assert resolved == sample_chain_config.chain_id

    async def test_resolve_backlink_chain_id_not_found(self, db: Database) -> None:
        with pytest.raises(DatabaseError):
            await db.resolve_backlink_chain_id("nonexistent")

    async def test_delete_chain_does_not_cascade_to_underlying_backlink_checks(
        self, db: Database, sample_chain_config: BacklinkChainConfig, tmp_path: _Path
    ) -> None:
        """Deleting a chain must never delete the backlink_checks/backlink_results
        rows its tiers point at -- those remain independently valid, addressable
        bl-check runs (see spec's `ON DELETE CASCADE` rationale)."""
        await db.create_backlink_chain(sample_chain_config)
        tier1_config = BacklinkCheckConfig(domain="medilana.id", input_path=tmp_path / "tier1.txt")
        await db.create_backlink_check(tier1_config, total_urls=1)
        await db.link_chain_tier(
            sample_chain_config.chain_id, 0, tier1_config.backlink_id, tmp_path / "tier1.txt"
        )

        conn = db._require_conn()
        await conn.execute(
            "DELETE FROM backlink_chains WHERE chain_id = ?", (sample_chain_config.chain_id,)
        )
        await conn.commit()

        assert await db.backlink_check_exists(tier1_config.backlink_id) is True
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM backlink_chain_tiers WHERE chain_id = ?",
            (sample_chain_config.chain_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row["n"] == 0


class TestBacklinkResultsMatchedTargetMigration:
    """`matched_target` was added to `backlink_results` after `robots_meta`/
    `robots_header` -- same additive `PRAGMA table_info` + `ALTER TABLE ADD
    COLUMN` migration, exercised here the same way
    `tests/test_crawl_database.py::TestCrawlLinksMigration` exercises
    `rel`/`target_attr` on `crawl_links`."""

    async def test_old_schema_db_gets_matched_target_column(
        self, tmp_path: _Path, sample_backlink_config: BacklinkCheckConfig
    ) -> None:
        """A `backlink_results` table created before `matched_target` existed
        should be upgraded via `ALTER TABLE` on connect, not raise 'no such
        column' or silently drop the value on save."""
        import aiosqlite

        db_path = tmp_path / "old.db"
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE backlink_checks (
                    backlink_id       TEXT PRIMARY KEY,
                    label             TEXT,
                    domain            TEXT NOT NULL,
                    input_path        TEXT NOT NULL,
                    status            TEXT NOT NULL,
                    config_json       TEXT NOT NULL,
                    started_at        TEXT NOT NULL,
                    finished_at       TEXT
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE backlink_results (
                    result_id         TEXT PRIMARY KEY,
                    backlink_id       TEXT NOT NULL,
                    source_url        TEXT NOT NULL,
                    final_url         TEXT,
                    status_code       INTEGER,
                    match_found       INTEGER NOT NULL DEFAULT 0,
                    match_type        TEXT NOT NULL DEFAULT 'not_found',
                    matched_href      TEXT,
                    rel               TEXT,
                    target            TEXT,
                    blocked           INTEGER NOT NULL DEFAULT 0,
                    requires_login    INTEGER NOT NULL DEFAULT 0,
                    text_mentions     INTEGER NOT NULL DEFAULT 0,
                    robots_meta       TEXT,
                    robots_header     TEXT,
                    notes             TEXT,
                    error             TEXT,
                    checked_at        TEXT NOT NULL
                );
                """
            )
            await conn.commit()

        database = Database(db_path)
        await database.connect()
        try:
            await database.create_backlink_check(sample_backlink_config, total_urls=1)
            await database.save_backlink_result(
                sample_backlink_config.backlink_id,
                BacklinkResult(
                    source_url="https://source.test/a",
                    match_found=True,
                    match_type="anchor",
                    matched_target="medilana.id",
                ),
            )
            [reloaded] = [
                r async for r in database.iter_backlink_results(sample_backlink_config.backlink_id)
            ]
            assert reloaded.matched_target == "medilana.id"
        finally:
            await database.close()

    async def test_pre_existing_rows_get_null_matched_target_after_migration(
        self, tmp_path: _Path, sample_backlink_config: BacklinkCheckConfig
    ) -> None:
        """A row inserted directly against the old (pre-`matched_target`)
        schema should read back as `matched_target=None` once a `Database`
        connects and migrates it, not raise or lose the row."""
        import aiosqlite

        db_path = tmp_path / "old.db"
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE backlink_checks (
                    backlink_id       TEXT PRIMARY KEY,
                    label             TEXT,
                    domain            TEXT NOT NULL,
                    input_path        TEXT NOT NULL,
                    status            TEXT NOT NULL,
                    config_json       TEXT NOT NULL,
                    started_at        TEXT NOT NULL,
                    finished_at       TEXT
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE backlink_results (
                    result_id         TEXT PRIMARY KEY,
                    backlink_id       TEXT NOT NULL,
                    source_url        TEXT NOT NULL,
                    final_url         TEXT,
                    status_code       INTEGER,
                    match_found       INTEGER NOT NULL DEFAULT 0,
                    match_type        TEXT NOT NULL DEFAULT 'not_found',
                    matched_href      TEXT,
                    rel               TEXT,
                    target            TEXT,
                    blocked           INTEGER NOT NULL DEFAULT 0,
                    requires_login    INTEGER NOT NULL DEFAULT 0,
                    text_mentions     INTEGER NOT NULL DEFAULT 0,
                    robots_meta       TEXT,
                    robots_header     TEXT,
                    notes             TEXT,
                    error             TEXT,
                    checked_at        TEXT NOT NULL
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO backlink_checks (
                    backlink_id, label, domain, input_path, status, config_json, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample_backlink_config.backlink_id,
                    sample_backlink_config.label,
                    sample_backlink_config.domain,
                    str(sample_backlink_config.input_path),
                    "running",
                    "{}",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            await conn.execute(
                """
                INSERT INTO backlink_results (
                    result_id, backlink_id, source_url, final_url, status_code,
                    match_found, match_type, matched_href, rel, target, blocked,
                    requires_login, text_mentions, robots_meta, robots_header,
                    notes, error, checked_at
                ) VALUES ('r1', ?, 'https://source.test/old', NULL, NULL, 0,
                          'not_found', NULL, NULL, NULL, 0, 0, 0, NULL, NULL,
                          NULL, NULL, '2026-01-01T00:00:00+00:00')
                """,
                (sample_backlink_config.backlink_id,),
            )
            await conn.commit()

        migrated_db = Database(db_path)
        await migrated_db.connect()
        try:
            [reloaded] = [
                r async for r in migrated_db.iter_backlink_results(sample_backlink_config.backlink_id)
            ]
            assert reloaded.source_url == "https://source.test/old"
            assert reloaded.matched_target is None
        finally:
            await migrated_db.close()


_HTML_DIRECT_ANCHOR = """<html><head><title>Post</title></head><body>
<a href="https://medilana.id/article" rel="nofollow sponsored" target="_blank">a link</a>
</body></html>"""

_HTML_ANCHOR_NO_ATTRS = """<html><head><title>Post</title></head><body>
<a href="https://medilana.id/article">a link</a>
</body></html>"""

_HTML_INDIRECT = """<html><head><title>Post</title></head><body>
<a href="https://redirector.test/go?to=medilana.id/article" rel="ugc" target="_self">tracker link</a>
</body></html>"""

_HTML_WITH_ROBOTS_META = """<html><head><title>Post</title>
<meta name="robots" content="noindex, follow"></head><body>
<a href="https://medilana.id/article">a link</a>
</body></html>"""

_HTML_NO_ROBOTS_META = """<html><head><title>Post</title></head><body>
<a href="https://medilana.id/article">a link</a>
</body></html>"""


class TestCheckOneCapturesRelAndTarget:
    """Ported from the now-deleted tests/test_backlink_checker.py."""

    async def test_direct_anchor_match_captures_rel_and_target(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HTML_DIRECT_ANCHOR)
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.match_type == "anchor"
        assert result.rel == "nofollow sponsored"
        assert result.target == "_blank"

    async def test_direct_anchor_match_with_no_attrs_leaves_both_none(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HTML_ANCHOR_NO_ATTRS)
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.match_type == "anchor"
        assert result.rel is None
        assert result.target is None

    async def test_indirect_match_captures_rel_and_target_of_the_tracker_anchor(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HTML_INDIRECT)
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.match_type == "indirect_query"
        assert result.rel == "ugc"
        assert result.target == "_self"


class TestCheckOneCapturesRobotsSignal:
    """robots_meta and robots_header are two independent signals -- see
    BacklinkResult.robots_header's docstring for why they aren't merged.
    """

    async def test_robots_meta_captured_when_present(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(
                    200, headers={"Content-Type": "text/html"}, text=_HTML_WITH_ROBOTS_META
                )
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.robots_meta == "noindex, follow"

    async def test_robots_meta_none_when_absent(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(
                    200, headers={"Content-Type": "text/html"}, text=_HTML_NO_ROBOTS_META
                )
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.robots_meta is None

    async def test_robots_header_captured_independently_of_meta_tag(self) -> None:
        """The header and the meta tag can disagree -- both must survive intact."""
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html", "X-Robots-Tag": "noarchive, nosnippet"},
                    text=_HTML_WITH_ROBOTS_META,  # meta says "noindex, follow" -- deliberately different
                )
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.robots_meta == "noindex, follow"
        assert result.robots_header == "noarchive, nosnippet"

    async def test_robots_header_none_when_absent(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://source.test/page").mock(
                return_value=httpx.Response(
                    200, headers={"Content-Type": "text/html"}, text=_HTML_NO_ROBOTS_META
                )
            )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                result = await check_one(
                    client, "https://source.test/page", frozenset({"medilana.id"}),
                    allow_subdomains=True, check_indirect=True,
                )

        assert result.robots_header is None


class TestPlaywrightNotInstalled:
    async def test_run_backlink_checks_browser_raises_clear_error_when_playwright_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "playwright.async_api":
                raise ImportError("No module named 'playwright'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", fake_import)

        async def on_result(_result: BacklinkResult) -> None:
            pass

        with pytest.raises(PlaywrightNotInstalledError, match="redirecthunter\\[js\\]"):
            await run_backlink_checks_browser(
                ["https://example.test/page"],
                frozenset({"medilana.id"}),
                concurrency=1,
                nav_timeout=10.0,
                render_wait=2.0,
                allow_subdomains=True,
                check_indirect=True,
                user_agent="test",
                headed=False,
                block_resources=True,
                on_result=on_result,
            )
    """check_one_browser / run_backlink_checks_browser, exercised against a
    real local HTTP server and a real (headless) Chromium via Playwright --
    not mocked. This is the mode that replaced backlink_checker_js.py; see
    MEMORY.md for why it's now a `bl-check --browser` flag instead of a
    separate script.
    """

    async def test_check_one_browser_captures_full_signal(self, local_html_server: str) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            try:
                result = await check_one_browser(
                    context,
                    f"{local_html_server}/direct.html",
                    frozenset({"medilana.id"}),
                    allow_subdomains=True,
                    check_indirect=True,
                    nav_timeout_ms=10_000,
                    render_wait_ms=2_000,
                )
            finally:
                await context.close()
                await browser.close()

        assert result.match_found is True
        assert result.match_type == "anchor"
        assert result.matched_href == "https://medilana.id/x"
        assert result.rel == "nofollow"
        assert result.target == "_blank"
        assert result.robots_meta == "noindex, follow"
        assert result.status_code == 200

    async def test_run_backlink_checks_browser_streams_results_via_on_result(
        self, local_html_server: str
    ) -> None:
        results: list[BacklinkResult] = []

        async def on_result(result: BacklinkResult) -> None:
            results.append(result)

        await run_backlink_checks_browser(
            [f"{local_html_server}/direct.html", f"{local_html_server}/no-match.html"],
            frozenset({"medilana.id"}),
            concurrency=2,
            nav_timeout=10.0,
            render_wait=2.0,
            allow_subdomains=True,
            check_indirect=True,
            user_agent="redirecthunter-test",
            headed=False,
            block_resources=True,
            on_result=on_result,
        )

        assert len(results) == 2
        by_url = {r.source_url: r for r in results}
        assert by_url[f"{local_html_server}/direct.html"].match_found is True
        assert by_url[f"{local_html_server}/no-match.html"].match_found is False
