#!/usr/bin/env python3
"""Generate real Rich-rendered SVG screenshots of the CLI for the README.

Not part of the package — a one-off documentation tool. Every image is
captured from genuinely executed CLI commands (via Typer's CliRunner) with
HTTP mocked through respx, so what's in docs/images/*.svg is real program
output, not a hand-drawn mockup.

Run with: python3 docs/generate_screenshots.py
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

import httpx
import respx
from rich.console import Console
from typer.testing import CliRunner

import redirecthunter.cli as cli_module

OUT_DIR = Path(__file__).parent / "images"
OUT_DIR.mkdir(exist_ok=True)

SVG_THEME_ARGS = dict(
    title="RedirectHunter",
)

runner = CliRunner(env={"COLUMNS": "118", "TERM": "xterm-256color"})


def capture(console: Console, filename: str) -> None:
    console.save_svg(str(OUT_DIR / filename), **SVG_THEME_ARGS)
    print(f"  wrote {filename}")


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="redirecthunter_screenshots_"))
    original_cwd = Path.cwd()
    urls_file = workdir / "urls.txt"
    urls_file.write_text(
        "https://example.com/go?url={TARGET}\n"
        "https://example.com/promo?dest={TARGET}\n"
        "https://example.com/legacy-link\n"
        "https://example.com/meta-splash\n"
        "https://example.com/js-splash\n"
        "https://broken.example.invalid/gone\n"
    )
    os.chdir(workdir)
    db_path = Path("redirecthunter.db")  # relative -- keeps screenshot titles clean

    record_console = Console(record=True, width=118)
    cli_module.console = record_console  # type: ignore[attr-defined]

    print("Capturing: scan")
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://example.com/go?url=https://example.org").mock(
            return_value=httpx.Response(
                301, headers={"Location": "https://mid.example.com/step", "Server": "nginx"}
            )
        )
        mock.get("https://mid.example.com/step").mock(
            return_value=httpx.Response(
                200,
                headers={"Server": "cloudflare", "CF-RAY": "8a1b2c3d4e5f6789-SIN", "Content-Type": "text/html"},
                text="<html><body>Welcome</body></html>",
            )
        )
        mock.get("https://example.com/promo?dest=https://example.org").mock(
            return_value=httpx.Response(200, headers={"Server": "Apache"}, text="<p>Promo landing page</p>")
        )
        mock.get("https://example.com/legacy-link").mock(
            return_value=httpx.Response(
                308, headers={"Location": "https://example.org/new-home", "Server": "LiteSpeed"}
            )
        )
        mock.get("https://example.org/new-home").mock(
            return_value=httpx.Response(200, headers={"Server": "nginx"}, text="<p>New home</p>")
        )
        mock.get("https://example.com/meta-splash").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text='<meta http-equiv="refresh" content="0;url=https://example.org/meta-target">',
            )
        )
        mock.get("https://example.org/meta-target").mock(
            return_value=httpx.Response(200, headers={"Server": "nginx"}, text="<p>Meta landing</p>")
        )
        mock.get("https://example.com/js-splash").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text='<script>window.location.href = "https://example.org/js-target";</script>',
            )
        )
        mock.get("https://example.org/js-target").mock(
            return_value=httpx.Response(200, headers={"Server": "nginx"}, text="<p>JS landing</p>")
        )
        mock.get("https://broken.example.invalid/gone").mock(side_effect=httpx.ConnectError("boom"))

        result = runner.invoke(
            cli_module.app,
            [
                "scan",
                str(urls_file),
                "--target",
                "https://example.org",
                "--method",
                "GET",
                "--workers",
                "6",
                "--no-http2",
                "--database",
                str(db_path),
                "--retry",
                "0",
            ],
        )
    if result.exit_code != 0:
        print(result.output)
        raise SystemExit(f"scan failed with exit code {result.exit_code}")

    match = re.search(r"Starting scan (\S+)", result.output)
    assert match is not None
    scan_id = match.group(1)
    capture(record_console, "scan_summary.svg")

    print("Capturing: show")
    result = runner.invoke(cli_module.app, ["show", scan_id, "--database", str(db_path)])
    assert result.exit_code == 0, result.output
    capture(record_console, "show_results.svg")

    print("Capturing: stats (all scans)")
    result = runner.invoke(cli_module.app, ["stats", "--database", str(db_path)])
    assert result.exit_code == 0, result.output
    capture(record_console, "stats_list.svg")

    shutil.rmtree(workdir, ignore_errors=True)
    os.chdir(original_cwd)
    print(f"\nDone. Screenshots written to {OUT_DIR}/")


if __name__ == "__main__":
    main()
