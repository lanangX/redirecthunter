"""Tests for redirecthunter.crawler.

Uses respx to mock the HTTP transport layer (same approach as
test_engine.py) so these run fully offline and deterministically.
"""

from __future__ import annotations

import httpx
import respx

from redirecthunter.crawler import Crawler, _dedupe_key, _looks_like_html
from redirecthunter.models import CrawlLinkResult, CrawlPageResult, LinkKind, PageIssue

_HOME_HTML = """<html><head><title>Home Page</title>
<meta name="description" content="The home page of our lovely test site."></head>
<body><h1>Welcome</h1>
<a href="/about">About</a>
<a href="/broken">Broken internal link</a>
<a href="https://external.test/page" rel="nofollow sponsored" target="_blank">External link</a>
<a href="/about">About again (duplicate on same page)</a>
<a href="#section">Same-page anchor, should be skipped</a>
<a href="javascript:void(0)">JS link, should be skipped</a>
</body></html>"""

_ABOUT_HTML = """<html><head><title>Home Page</title></head>
<body><h1>About Us</h1><h1>Second H1</h1>
<a href="/">Home</a>
</body></html>"""

_NO_SIGNALS_HTML = """<html><head></head><body>No title, meta, or H1 here.</body></html>"""


class TestDedupeKey:
    def test_strips_fragment(self) -> None:
        assert _dedupe_key("https://x.test/page#frag", include_query=True) == "https://x.test/page"

    def test_keeps_query_by_default(self) -> None:
        assert _dedupe_key("https://x.test/page?a=1", include_query=True) == "https://x.test/page?a=1"

    def test_drops_query_when_disabled(self) -> None:
        assert _dedupe_key("https://x.test/page?a=1", include_query=False) == "https://x.test/page"
        assert _dedupe_key("https://x.test/page?a=1", include_query=False) == _dedupe_key(
            "https://x.test/page?a=2", include_query=False
        )


class TestLooksLikeHtml:
    def test_trusts_html_content_type(self) -> None:
        assert _looks_like_html("text/html; charset=utf-8", "irrelevant") is True

    def test_rejects_explicit_non_html_type(self) -> None:
        assert _looks_like_html("application/pdf", "<html>") is False

    def test_sniffs_when_content_type_missing(self) -> None:
        assert _looks_like_html(None, "<html><body>hi</body></html>") is True
        assert _looks_like_html(None, "just plain text") is False


class TestCrawlerClassify:
    def test_seed_host_is_internal(self, sample_crawl_config) -> None:
        crawler = Crawler(sample_crawl_config)
        crawler._reference_domains = {"example.test"}
        assert crawler._classify("https://example.test/anything") is LinkKind.INTERNAL

    def test_other_host_is_external(self, sample_crawl_config) -> None:
        crawler = Crawler(sample_crawl_config)
        crawler._reference_domains = {"example.test"}
        assert crawler._classify("https://external.test/page") is LinkKind.EXTERNAL

    def test_subdomain_is_internal(self, sample_crawl_config) -> None:
        crawler = Crawler(sample_crawl_config)
        crawler._reference_domains = {"example.test"}
        assert crawler._classify("https://blog.example.test/post") is LinkKind.INTERNAL

    def test_allowed_domains_extend_scope(self, sample_crawl_config) -> None:
        config = sample_crawl_config.model_copy(update={"allowed_domains": ["partner.test"]})
        crawler = Crawler(config)
        crawler._reference_domains = {"example.test", "partner.test"}
        assert crawler._classify("https://partner.test/page") is LinkKind.INTERNAL


class TestCrawlerRun:
    async def test_discovers_and_persists_pages_and_links(self, sample_crawl_config) -> None:
        """Full BFS run over a tiny mocked site: home -> about -> home (cycle), plus a
        broken internal link (promoted to a dead page) and an external link (checked)."""
        crawler = Crawler(sample_crawl_config)
        pages: list[CrawlPageResult] = []
        links: list[CrawlLinkResult] = []

        async def on_page(page: CrawlPageResult) -> None:
            pages.append(page)

        async def on_link(link: CrawlLinkResult) -> None:
            links.append(link)

        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://example.test/").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HOME_HTML)
            )
            mock.get("https://example.test/about").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_ABOUT_HTML)
            )
            mock.get("https://example.test/broken").mock(return_value=httpx.Response(404, text="not found"))
            mock.head("https://example.test/").mock(return_value=httpx.Response(200))
            mock.head("https://external.test/page").mock(return_value=httpx.Response(200))

            stats = await crawler.run(["https://example.test/"], on_page, on_link)

        by_url = {p.url: p for p in pages}
        assert set(by_url) == {
            "https://example.test/",
            "https://example.test/about",
            "https://example.test/broken",
        }

        home = by_url["https://example.test/"]
        assert home.title == "Home Page"
        assert home.meta_description == "The home page of our lovely test site."
        assert home.h1_texts == ["Welcome"]
        # /about appears twice as a raw href on the home page -- deduplicated to one link.
        assert home.internal_link_count == 2  # /about, /broken
        assert home.external_link_count == 1  # external.test/page
        assert PageIssue.TITLE_TOO_SHORT in home.issues

        about = by_url["https://example.test/about"]
        assert about.h1_count == 2
        assert PageIssue.MULTIPLE_H1 in about.issues
        assert PageIssue.MISSING_META_DESCRIPTION in about.issues

        broken = by_url["https://example.test/broken"]
        assert broken.status_code == 404
        assert broken.alive is True  # got a real HTTP response, just an error status
        assert PageIssue.MISSING_TITLE in broken.issues

        # The only thing that should show up in crawl_links here is the second
        # occurrence of the home page (linked again from /about) and the
        # external link -- /about and /broken were each promoted straight to
        # a page fetch on their first (only) occurrence.
        link_targets = {(link.source_page_url, link.target_url, link.link_kind) for link in links}
        assert ("https://example.test/about", "https://example.test/", LinkKind.INTERNAL) in link_targets
        assert (
            "https://example.test/",
            "https://external.test/page",
            LinkKind.EXTERNAL,
        ) in link_targets
        assert all(not link.is_broken for link in links)

        # The external link carries rel/target attributes straight from its <a> tag.
        external_link = next(link for link in links if link.target_url == "https://external.test/page")
        assert external_link.rel == "nofollow sponsored"
        assert external_link.target_attr == "_blank"

        # The home->about occurrence had no rel/target attributes on its <a>.
        about_link = next(link for link in links if link.target_url == "https://example.test/")
        assert about_link.rel is None
        assert about_link.target_attr is None

        assert stats.pages_completed == 3
        assert stats.pages_alive == 3
        assert stats.pages_dead == 0

    async def test_respects_max_pages_budget(self, sample_crawl_config) -> None:
        """Internal links beyond the page budget are still checked, just not crawled further."""
        config = sample_crawl_config.model_copy(update={"max_pages": 1, "check_external_links": False})
        crawler = Crawler(config)
        pages: list[CrawlPageResult] = []
        links: list[CrawlLinkResult] = []

        async def on_page(page: CrawlPageResult) -> None:
            pages.append(page)

        async def on_link(link: CrawlLinkResult) -> None:
            links.append(link)

        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://example.test/").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HOME_HTML)
            )
            mock.head("https://example.test/about").mock(return_value=httpx.Response(200))
            mock.head("https://example.test/broken").mock(return_value=httpx.Response(404))

            await crawler.run(["https://example.test/"], on_page, on_link)

        assert len(pages) == 1
        assert pages[0].url == "https://example.test/"
        checked_targets = {link.target_url for link in links}
        assert "https://example.test/about" in checked_targets
        assert "https://example.test/broken" in checked_targets
        broken_link = next(link for link in links if link.target_url == "https://example.test/broken")
        assert broken_link.is_broken is True

    async def test_no_follow_links_checks_but_does_not_crawl(self, sample_crawl_config) -> None:
        """--no-follow-links: seed page is fetched/audited, its links checked, never expanded."""
        config = sample_crawl_config.model_copy(update={"follow_links": False, "check_external_links": False})
        crawler = Crawler(config)
        pages: list[CrawlPageResult] = []
        links: list[CrawlLinkResult] = []

        async def on_page(page: CrawlPageResult) -> None:
            pages.append(page)

        async def on_link(link: CrawlLinkResult) -> None:
            links.append(link)

        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://example.test/").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=_HOME_HTML)
            )
            mock.head("https://example.test/about").mock(return_value=httpx.Response(200))
            mock.head("https://example.test/broken").mock(return_value=httpx.Response(404))

            await crawler.run(["https://example.test/"], on_page, on_link)

        assert len(pages) == 1  # only the seed -- /about and /broken never crawled
        assert {link.target_url for link in links} == {
            "https://example.test/about",
            "https://example.test/broken",
        }

    async def test_dead_seed_is_reported_not_raised(self, sample_crawl_config) -> None:
        """A transport-level failure on a page produces a dead CrawlPageResult, not an exception."""
        crawler = Crawler(sample_crawl_config)
        pages: list[CrawlPageResult] = []

        async def on_page(page: CrawlPageResult) -> None:
            pages.append(page)

        async def on_link(_link: CrawlLinkResult) -> None:
            pass

        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://example.test/").mock(side_effect=httpx.ConnectError("refused"))
            stats = await crawler.run(["https://example.test/"], on_page, on_link)

        assert len(pages) == 1
        assert pages[0].alive is False
        assert pages[0].error is not None
        assert stats.pages_dead == 1

    async def test_link_status_is_cached_across_source_pages(self, sample_crawl_config) -> None:
        """The same external target linked from two pages is only requested once."""
        config = sample_crawl_config.model_copy(update={"max_depth": 5, "max_pages": 10})
        crawler = Crawler(config)
        links: list[CrawlLinkResult] = []

        two_page_home = _HOME_HTML.replace(
            "<a href=\"/about\">About again (duplicate on same page)</a>",
            "",
        )

        async def on_page(_page: CrawlPageResult) -> None:
            pass

        async def on_link(link: CrawlLinkResult) -> None:
            links.append(link)

        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://example.test/").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=two_page_home)
            )
            mock.get("https://example.test/about").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text=_ABOUT_HTML.replace(
                        "<a href=\"/\">Home</a>",
                        '<a href="/">Home</a><a href="https://external.test/page">Ext</a>',
                    ),
                )
            )
            external_route = mock.head("https://external.test/page").mock(return_value=httpx.Response(200))

            await crawler.run(["https://example.test/"], on_page, on_link)

        # Two occurrences recorded (home->external, about->external)...
        external_occurrences = [link for link in links if link.target_url == "https://external.test/page"]
        assert len(external_occurrences) == 2
        # ...but only one real network request was made for it.
        assert external_route.call_count == 1

    async def test_cached_link_status_still_carries_per_occurrence_rel_and_target(
        self, sample_crawl_config
    ) -> None:
        """A cache hit reuses the HTTP status but each occurrence keeps its own
        rel/target attributes -- e.g. home links rel="sponsor", about links plain."""
        config = sample_crawl_config.model_copy(update={"max_depth": 5, "max_pages": 10})
        crawler = Crawler(config)
        links: list[CrawlLinkResult] = []

        home_html = _HOME_HTML.replace(
            '<a href="https://external.test/page" rel="nofollow sponsored" target="_blank">External link</a>',
            '<a href="https://external.test/page" rel="sponsored" target="_blank">External link</a>',
        ).replace(
            '<a href="/about">About again (duplicate on same page)</a>',
            "",
        )
        about_html = _ABOUT_HTML.replace(
            '<a href="/">Home</a>',
            '<a href="/">Home</a><a href="https://external.test/page">Ext, no rel here</a>',
        )

        async def on_page(_page: CrawlPageResult) -> None:
            pass

        async def on_link(link: CrawlLinkResult) -> None:
            links.append(link)

        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://example.test/").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=home_html)
            )
            mock.get("https://example.test/about").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=about_html)
            )
            mock.head("https://external.test/page").mock(return_value=httpx.Response(200))

            await crawler.run(["https://example.test/"], on_page, on_link)

        external_occurrences = {
            link.source_page_url: link for link in links if link.target_url == "https://external.test/page"
        }
        assert external_occurrences["https://example.test/"].rel == "sponsored"
        assert external_occurrences["https://example.test/"].target_attr == "_blank"
        assert external_occurrences["https://example.test/about"].rel is None
        assert external_occurrences["https://example.test/about"].target_attr is None

