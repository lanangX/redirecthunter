"""Shared pytest fixtures for the RedirectHunter test suite."""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from redirecthunter.models import CrawlConfig, CrawlSeedMode, HTTPMethod, InputFormat, ScanConfig


@pytest.fixture
def sample_config(tmp_path: Path) -> ScanConfig:
    """A minimal, valid ScanConfig pointing at a temp directory.

    Individual tests override fields via ``.model_copy(update={...})``
    rather than constructing a new config from scratch each time.
    """
    input_file = tmp_path / "urls.txt"
    input_file.write_text("https://example.com/go?url={TARGET}\n")
    return ScanConfig(
        input_path=input_file,
        input_format=InputFormat.TXT,
        target="https://example.org",
        method=HTTPMethod.HEAD,
        workers=5,
        timeout=5.0,
        connect_timeout=3.0,
        http2=False,
        database_path=tmp_path / "redirecthunter.db",
    )


@pytest.fixture
def sample_crawl_config(tmp_path: Path) -> CrawlConfig:
    """A minimal, valid CrawlConfig for domain-mode crawling in tests."""
    return CrawlConfig(
        seed_mode=CrawlSeedMode.DOMAIN,
        seed_url="https://example.test/",
        max_depth=5,
        max_pages=50,
        workers=5,
        timeout=5.0,
        connect_timeout=3.0,
        http2=False,
        database_path=tmp_path / "redirecthunter.db",
    )


_DIRECT_HTML = """<html><head><title>Post</title>
<meta name="robots" content="noindex, follow"></head>
<body><a href="https://medilana.id/x" rel="nofollow" target="_blank">link</a></body></html>"""

_NO_MATCH_HTML = """<html><head><title>Post</title></head>
<body><a href="https://elsewhere.test/x">unrelated link</a></body></html>"""


@pytest.fixture
def local_html_server(tmp_path: Path) -> Iterator[str]:
    """A real local HTTP server serving fixed HTML, for Playwright-mode tests.

    check_one_browser/run_backlink_checks_browser drive an actual
    (headless) Chromium -- respx can't intercept browser-level
    navigation the way it does httpx, so browser-mode tests need a real
    server to point the browser at instead of a mocked response.
    """
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "direct.html").write_text(_DIRECT_HTML)
    (site_dir / "no-match.html").write_text(_NO_MATCH_HTML)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
