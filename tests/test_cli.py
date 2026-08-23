"""Tests for redirecthunter.cli, using Typer's CliRunner + respx (no real sockets)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from redirecthunter.cli import (
    _build_per_url_targets,
    _parse_account_headers,
    _parse_status_filter,
    _validate_account_references,
    app,
)
from redirecthunter.database import Database
from redirecthunter.models import (
    CandidateURL,
    HTTPMethod,
    InputFormat,
    RedirectResult,
    RunStatus,
    ScanConfig,
)

runner = CliRunner(env={"COLUMNS": "220", "TERM": "xterm-256color"})


def _write_urls(tmp_path: Path) -> Path:
    f = tmp_path / "urls.txt"
    f.write_text("https://example.test/direct\nhttps://example.test/redirect\n")
    return f


def _init_empty_db(db_path: Path) -> None:
    async def _init() -> None:
        db = Database(db_path)
        await db.connect()
        await db.close()

    asyncio.run(_init())


class TestParseStatusFilter:
    def test_exact_codes(self) -> None:
        codes, classes = _parse_status_filter(["301", "404"])
        assert codes == frozenset({301, 404})
        assert classes == frozenset()

    def test_classes(self) -> None:
        codes, classes = _parse_status_filter(["3xx", "4XX"])
        assert codes == frozenset()
        assert classes == frozenset({3, 4})

    def test_comma_separated_and_mixed(self) -> None:
        codes, classes = _parse_status_filter(["301,302", "4xx"])
        assert codes == frozenset({301, 302})
        assert classes == frozenset({4})

    def test_malformed_token_is_skipped_not_raised(self) -> None:
        codes, classes = _parse_status_filter(["301", "not-a-code"])
        assert codes == frozenset({301})
        assert classes == frozenset()

    def test_none_input_returns_empty(self) -> None:
        codes, classes = _parse_status_filter(None)
        assert codes == frozenset()
        assert classes == frozenset()


class TestBuildPerUrlTargets:
    """The multi-target (``;``-separated) per-row override, as built for
    ``run_backlink_checks``/``run_backlink_checks_browser``'s
    ``per_url_targets`` parameter."""

    def test_no_override_omitted(self) -> None:
        candidates = [CandidateURL(raw_url="https://a.com")]
        assert _build_per_url_targets(candidates) == {}

    def test_single_element_tuple_matches_pre_multi_target_behavior(self) -> None:
        """Regression guard: a one-element override must produce the exact
        same one-element frozenset it always has, whether the loader handed
        it over as a bare string (legacy shape) or a one-element tuple
        (current loader shape)."""
        candidates = [CandidateURL(raw_url="https://a.com", row_metadata={"target": ("Medilana.ID",)})]
        assert _build_per_url_targets(candidates) == {"https://a.com": frozenset({"medilana.id"})}

        legacy_shape = [CandidateURL(raw_url="https://a.com", row_metadata={"target": "Medilana.ID"})]
        assert _build_per_url_targets(legacy_shape) == {"https://a.com": frozenset({"medilana.id"})}

    def test_multi_element_tuple_builds_multi_member_frozenset(self) -> None:
        candidates = [
            CandidateURL(
                raw_url="https://a.com",
                row_metadata={"target": ("medilana.co.id", "form.medilana.com", "img.medilana.my.id")},
            )
        ]
        result = _build_per_url_targets(candidates)
        assert result == {
            "https://a.com": frozenset({"medilana.co.id", "form.medilana.com", "img.medilana.my.id"})
        }

    def test_only_rows_with_override_appear(self) -> None:
        candidates = [
            CandidateURL(raw_url="https://a.com", row_metadata={"target": ("medilana.id",)}),
            CandidateURL(raw_url="https://b.com"),
        ]
        result = _build_per_url_targets(candidates)
        assert list(result.keys()) == ["https://a.com"]


class TestParseAccountHeaders:
    """``--accounts-file`` parser: ``account_id|Name: Value`` per line."""

    def test_multiple_headers_for_one_account(self) -> None:
        registry, warnings = _parse_account_headers(
            [
                "account_001|Cookie: session=xxxxx",
                "account_001|User-Agent: Mozilla/5.0",
            ]
        )
        assert registry == {
            "account_001": {"Cookie": "session=xxxxx", "User-Agent": "Mozilla/5.0"}
        }
        assert warnings == []

    def test_multiple_accounts(self) -> None:
        registry, warnings = _parse_account_headers(
            ["account_001|Cookie: session=xxxxx", "account_057|Cookie: session=yyyyy"]
        )
        assert registry == {
            "account_001": {"Cookie": "session=xxxxx"},
            "account_057": {"Cookie": "session=yyyyy"},
        }
        assert warnings == []

    def test_bare_account_line_registers_with_no_headers(self) -> None:
        registry, warnings = _parse_account_headers(["account_002|"])
        assert registry == {"account_002": {}}
        assert warnings == []

    def test_malformed_line_is_skipped_with_warning_not_crash(self) -> None:
        registry, warnings = _parse_account_headers(
            ["account_001|Cookie: session=xxxxx", "this line has no pipe or colon"]
        )
        assert registry == {"account_001": {"Cookie": "session=xxxxx"}}
        assert len(warnings) == 1

    def test_none_input_returns_empty(self) -> None:
        registry, warnings = _parse_account_headers(None)
        assert registry == {}
        assert warnings == []


class TestValidateAccountReferences:
    def test_missing_accounts_returned_sorted(self) -> None:
        missing = _validate_account_references(
            {"https://a.com": "account_002", "https://b.com": "account_001"},
            {"account_001": {"Cookie": "x"}},
        )
        assert missing == ["account_002"]

    def test_all_matched_returns_empty(self) -> None:
        missing = _validate_account_references(
            {"https://a.com": "account_001"}, {"account_001": {"Cookie": "x"}}
        )
        assert missing == []

    def test_no_references_returns_empty(self) -> None:
        assert _validate_account_references({}, {"account_001": {"Cookie": "x"}}) == []


class TestScanCommand:
    def test_successful_scan(self, tmp_path: Path) -> None:
        urls_file = _write_urls(tmp_path)
        db_path = tmp_path / "scan.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/direct").mock(return_value=httpx.Response(200, text="ok"))
            mock.get("https://example.test/redirect").mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.test/direct"})
            )
            result = runner.invoke(
                app,
                [
                    "scan",
                    str(urls_file),
                    "--method",
                    "GET",
                    "--workers",
                    "2",
                    "--no-http2",
                    "--database",
                    str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Scan Summary" in result.output
        assert db_path.exists()

    def test_missing_input_file_rejected_by_typer(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["scan", str(tmp_path / "nope.txt")])
        assert result.exit_code != 0

    def test_config_error_from_bad_yaml_reported_cleanly(self, tmp_path: Path) -> None:
        urls_file = _write_urls(tmp_path)
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("workers: 99999\n")  # exceeds ScanConfig's max
        result = runner.invoke(app, ["scan", str(urls_file), "--config", str(bad_config)])
        assert result.exit_code == 1
        assert "Configuration error" in result.output

    def test_empty_input_file_is_a_clean_noop(self, tmp_path: Path) -> None:
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        result = runner.invoke(app, ["scan", str(empty_file)])
        assert result.exit_code == 0
        assert "Nothing to do" in result.output


class TestFullWorkflow:
    def test_scan_then_stats_show_export(self, tmp_path: Path) -> None:
        urls_file = _write_urls(tmp_path)
        db_path = tmp_path / "scan.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/direct").mock(
                return_value=httpx.Response(200, text="ok", headers={"Server": "nginx"})
            )
            mock.get("https://example.test/redirect").mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.test/direct"})
            )
            scan_result = runner.invoke(
                app,
                [
                    "scan",
                    str(urls_file),
                    "--method",
                    "GET",
                    "--workers",
                    "2",
                    "--no-http2",
                    "--database",
                    str(db_path),
                ],
            )
        assert scan_result.exit_code == 0, scan_result.output

        match = re.search(r"Starting scan (\S+)", scan_result.output)
        assert match is not None
        scan_id = match.group(1)

        stats_result = runner.invoke(app, ["stats", scan_id, "--database", str(db_path)])
        assert stats_result.exit_code == 0
        assert "Scan Summary" in stats_result.output

        stats_all_result = runner.invoke(app, ["stats", "--database", str(db_path)])
        assert stats_all_result.exit_code == 0
        # Rich truncates the Scan ID column under CliRunner's narrow default terminal
        # width, so only the ID's prefix is guaranteed to appear verbatim.
        assert scan_id[:8] in stats_all_result.output

        show_result = runner.invoke(app, ["show", scan_id, "--database", str(db_path)])
        assert show_result.exit_code == 0
        assert "example.test" in show_result.output

        show_filtered = runner.invoke(app, ["show", scan_id, "--database", str(db_path), "--redirects-only"])
        assert show_filtered.exit_code == 0

        csv_out = tmp_path / "out.csv"
        export_result = runner.invoke(app, ["export", scan_id, str(csv_out), "--database", str(db_path)])
        assert export_result.exit_code == 0, export_result.output
        assert csv_out.exists()

        json_out = tmp_path / "out.json"
        export_json_result = runner.invoke(
            app, ["export", scan_id, str(json_out), "--format", "json", "--database", str(db_path)]
        )
        assert export_json_result.exit_code == 0
        assert json_out.exists()

        show_status_filtered = runner.invoke(
            app, ["show", scan_id, "--database", str(db_path), "--status-code", "301"]
        )
        assert show_status_filtered.exit_code == 0
        assert "301" in show_status_filtered.output

        redirects_csv = tmp_path / "redirects_301.csv"
        export_status_result = runner.invoke(
            app,
            ["export", scan_id, str(redirects_csv), "--database", str(db_path), "--status-code", "3xx"],
        )
        assert export_status_result.exit_code == 0, export_status_result.output
        assert "Exported 1 results" in export_status_result.output

    def test_head_method_scan_warns_about_body_detection(self, tmp_path: Path) -> None:
        urls_file = _write_urls(tmp_path)
        db_path = tmp_path / "scan.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.head("https://example.test/direct").mock(return_value=httpx.Response(200))
            mock.head("https://example.test/redirect").mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.test/direct"})
            )
            # No --method passed: HTTPMethod.HEAD is the default.
            scan_result = runner.invoke(
                app,
                ["scan", str(urls_file), "--workers", "2", "--no-http2", "--database", str(db_path)],
            )

        assert scan_result.exit_code == 0, scan_result.output
        assert "method=HEAD has no response body" in scan_result.output
        assert "--has-link-only" in scan_result.output


class TestResumeCommand:
    def test_resume_continues_interrupted_scan(self, tmp_path: Path) -> None:
        urls_file = _write_urls(tmp_path)
        db_path = tmp_path / "resume.db"

        async def setup() -> str:
            config = ScanConfig(
                input_path=urls_file,
                input_format=InputFormat.TXT,
                method=HTTPMethod.GET,
                workers=2,
                http2=False,
                database_path=db_path,
            )
            db = Database(db_path)
            await db.connect()
            await db.create_scan(config, total_urls=2)
            await db.save_result(
                RedirectResult(
                    scan_id=config.scan_id,
                    source_url="https://example.test/direct",
                    expanded_url="https://example.test/direct",
                    http_method=HTTPMethod.GET,
                    status_code=200,
                    alive=True,
                    latency_ms=5.0,
                )
            )
            await db.update_scan_status(config.scan_id, RunStatus.INTERRUPTED)
            await db.close()
            return config.scan_id

        scan_id = asyncio.run(setup())

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/redirect").mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.test/direct"})
            )
            result = runner.invoke(app, ["resume", scan_id, "--database", str(db_path)])

        assert result.exit_code == 0, result.output
        assert "already completed" in result.output

    def test_resume_nonexistent_scan(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        _init_empty_db(db_path)
        result = runner.invoke(app, ["resume", "nonexistent-scan-id", "--database", str(db_path)])
        assert result.exit_code == 1


class TestErrorPaths:
    def test_stats_missing_database(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["stats", "--database", str(tmp_path / "nope.db")])
        assert result.exit_code == 1

    def test_stats_empty_database_lists_nothing(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        _init_empty_db(db_path)
        result = runner.invoke(app, ["stats", "--database", str(db_path)])
        assert result.exit_code == 0
        assert "No scans recorded" in result.output

    def test_show_missing_scan(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        _init_empty_db(db_path)
        result = runner.invoke(app, ["show", "nonexistent-scan-id", "--database", str(db_path)])
        assert result.exit_code == 1

    def test_export_missing_database(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["export", "some-id", str(tmp_path / "out.csv"), "--database", str(tmp_path / "nope.db")]
        )
        assert result.exit_code == 1

    def test_export_missing_scan(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        _init_empty_db(db_path)
        result = runner.invoke(
            app, ["export", "nonexistent-scan-id", str(tmp_path / "out.csv"), "--database", str(db_path)]
        )
        assert result.exit_code == 1


class TestFindCommand:
    """Covers the `find` command and short (prefix) scan_id resolution end-to-end."""

    def _scan_with_mixed_redirects(self, tmp_path: Path) -> tuple[Path, str]:
        """Run a real scan (via CliRunner) where some redirects land on the target
        domain and some land elsewhere, entirely through respx (no real network,
        including for the 'external' destinations)."""
        urls_file = tmp_path / "urls.txt"
        urls_file.write_text(
            "https://example.test/ext\n"
            "https://example.test/int\n"
            "https://example.test/sub\n"
            "https://example.test/tricky\n"
            "https://example.test/direct\n"
        )
        db_path = tmp_path / "scan.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/ext").mock(
                return_value=httpx.Response(301, headers={"Location": "https://evil-tracker.com/steal"})
            )
            mock.get("https://evil-tracker.com/steal").mock(return_value=httpx.Response(200, text="stolen"))
            mock.get("https://example.test/int").mock(
                return_value=httpx.Response(302, headers={"Location": "https://example.org/landing"})
            )
            mock.get("https://example.org/landing").mock(return_value=httpx.Response(200, text="landed"))
            mock.get("https://example.test/sub").mock(
                return_value=httpx.Response(301, headers={"Location": "https://sub.example.org/page"})
            )
            mock.get("https://sub.example.org/page").mock(return_value=httpx.Response(200, text="sub landed"))
            mock.get("https://example.test/tricky").mock(
                return_value=httpx.Response(302, headers={"Location": "https://notexample.org.evil.com/phish"})
            )
            mock.get("https://notexample.org.evil.com/phish").mock(return_value=httpx.Response(200, text="phish"))
            mock.get("https://example.test/direct").mock(return_value=httpx.Response(200, text="direct"))

            result = runner.invoke(
                app,
                [
                    "scan",
                    str(urls_file),
                    "--target",
                    "https://example.org",
                    "--method",
                    "GET",
                    "--workers",
                    "5",
                    "--no-http2",
                    "--database",
                    str(db_path),
                ],
            )
        assert result.exit_code == 0, result.output
        match = re.search(r"Starting scan (\S+)", result.output)
        assert match is not None
        return db_path, match.group(1)

    def test_find_auto_detects_domain_and_excludes_target_and_subdomain(self, tmp_path: Path) -> None:
        db_path, scan_id = self._scan_with_mixed_redirects(tmp_path)
        result = runner.invoke(app, ["find", scan_id, "--database", str(db_path)])
        assert result.exit_code == 0, result.output
        assert "evil-tracker.com" in result.output
        assert "notexample.org.evil.com" in result.output
        # example.org and sub.example.org destinations must NOT appear as "external"
        assert "example.org/landing" not in result.output
        assert "sub.example.org/page" not in result.output

    def test_find_explicit_domain_overrides_auto_detection(self, tmp_path: Path) -> None:
        db_path, scan_id = self._scan_with_mixed_redirects(tmp_path)
        # Treat evil-tracker.com itself as the "home" domain -- only the other
        # external destination should now be reported.
        result = runner.invoke(
            app, ["find", scan_id, "--database", str(db_path), "--domain", "evil-tracker.com"]
        )
        assert result.exit_code == 0, result.output
        assert "evil-tracker.com" not in result.output.replace("outside 'evil-tracker.com'", "")
        assert "notexample.org.evil.com" in result.output

    def test_find_output_writes_plain_link_list_only(self, tmp_path: Path) -> None:
        db_path, scan_id = self._scan_with_mixed_redirects(tmp_path)
        output_file = tmp_path / "external.txt"
        result = runner.invoke(
            app, ["find", scan_id, "--database", str(db_path), "--output", str(output_file)]
        )
        assert result.exit_code == 0, result.output
        assert output_file.exists()

        lines = output_file.read_text().strip().splitlines()
        assert len(lines) == 2
        assert all(line.startswith("https://") for line in lines)
        assert "https://evil-tracker.com/steal" in lines
        assert "https://notexample.org.evil.com/phish" in lines
        # Plain link list -- no table borders, no source URLs, no headers.
        assert "┃" not in output_file.read_text()
        assert "example.test" not in output_file.read_text()

    def test_find_output_field_both_includes_source(self, tmp_path: Path) -> None:
        db_path, scan_id = self._scan_with_mixed_redirects(tmp_path)
        output_file = tmp_path / "external_with_source.txt"
        result = runner.invoke(
            app,
            [
                "find",
                scan_id,
                "--database",
                str(db_path),
                "--output",
                str(output_file),
                "--field",
                "both",
            ],
        )
        assert result.exit_code == 0, result.output
        content = output_file.read_text()
        assert "https://example.test/ext -> https://evil-tracker.com/steal" in content

    def test_find_output_field_source_only(self, tmp_path: Path) -> None:
        db_path, scan_id = self._scan_with_mixed_redirects(tmp_path)
        output_file = tmp_path / "sources_only.txt"
        result = runner.invoke(
            app,
            [
                "find",
                scan_id,
                "--database",
                str(db_path),
                "--output",
                str(output_file),
                "--field",
                "source",
            ],
        )
        assert result.exit_code == 0, result.output
        lines = output_file.read_text().strip().splitlines()
        assert lines == ["https://example.test/ext", "https://example.test/tricky"]
        # No destinations, no arrows -- source URLs only.
        assert "evil-tracker.com" not in output_file.read_text()
        assert "->" not in output_file.read_text()

    def test_find_invert_shows_matching_domain_instead_of_external(self, tmp_path: Path) -> None:
        db_path, scan_id = self._scan_with_mixed_redirects(tmp_path)
        result = runner.invoke(app, ["find", scan_id, "--database", str(db_path), "--invert"])
        assert result.exit_code == 0, result.output
        # With --invert: internal destinations (example.org, sub.example.org) show up...
        assert "example.org/landing" in result.output
        assert "sub.example.org/page" in result.output
        # ...and the external ones from the non-inverted test must NOT appear.
        assert "evil-tracker.com" not in result.output
        assert "notexample.org.evil.com" not in result.output

    def test_find_invert_with_output_source_only(self, tmp_path: Path) -> None:
        """Reproduces the exact real-world workflow: confirm which source URLs
        genuinely redirect to the target domain, saving just those source URLs."""
        db_path, scan_id = self._scan_with_mixed_redirects(tmp_path)
        output_file = tmp_path / "confirmed_backlinks.txt"
        result = runner.invoke(
            app,
            [
                "find",
                scan_id,
                "--database",
                str(db_path),
                "--invert",
                "--field",
                "source",
                "--output",
                str(output_file),
            ],
        )
        assert result.exit_code == 0, result.output
        lines = sorted(output_file.read_text().strip().splitlines())
        assert lines == sorted(["https://example.test/int", "https://example.test/sub"])

    def test_find_no_early_break_generator_cleanup_is_clean(self, tmp_path: Path) -> None:
        """Regression test for the abandoned-async-generator bug: `show` used to
        leave db.iter_results() unclosed when breaking early at --limit, causing
        a 'Cannot operate on a closed database' traceback dumped to stderr after
        the command had already finished and printed its output. Verifies the
        command completes cleanly with a low --limit against a scan with more
        results than the limit."""
        urls_file = tmp_path / "urls.txt"
        urls_file.write_text("\n".join(f"https://example.test/r{i}" for i in range(10)) + "\n")
        db_path = tmp_path / "scan.db"
        with respx.mock(assert_all_called=True) as mock:
            for i in range(10):
                mock.get(f"https://example.test/r{i}").mock(return_value=httpx.Response(200, text="ok"))
            scan_result = runner.invoke(
                app,
                ["scan", str(urls_file), "--method", "GET", "--no-http2", "--database", str(db_path)],
            )
        assert scan_result.exit_code == 0
        scan_id = re.search(r"Starting scan (\S+)", scan_result.output).group(1)

        result = runner.invoke(app, ["show", scan_id, "--database", str(db_path), "--limit", "3"])
        assert result.exit_code == 0, result.output
        assert "Showing 3 result(s)" in result.output

    def test_find_with_short_scan_id(self, tmp_path: Path) -> None:
        db_path, scan_id = self._scan_with_mixed_redirects(tmp_path)
        short_id = scan_id[:8]
        result = runner.invoke(app, ["find", short_id, "--database", str(db_path)])
        assert result.exit_code == 0, result.output
        assert "evil-tracker.com" in result.output

    def test_find_no_target_and_no_explicit_domain_errors_cleanly(self, tmp_path: Path) -> None:
        urls_file = tmp_path / "urls.txt"
        urls_file.write_text("https://example.test/direct\n")
        db_path = tmp_path / "scan.db"
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/direct").mock(return_value=httpx.Response(200, text="ok"))
            scan_result = runner.invoke(
                app,
                ["scan", str(urls_file), "--method", "GET", "--no-http2", "--database", str(db_path)],
            )
        assert scan_result.exit_code == 0
        match = re.search(r"Starting scan (\S+)", scan_result.output)
        scan_id = match.group(1)

        result = runner.invoke(app, ["find", scan_id, "--database", str(db_path)])
        assert result.exit_code == 1
        assert "Could not auto-detect" in result.output


class TestShortScanIdAcrossCommands:
    """Short scan_id prefixes must work for show/export, not just find."""

    def test_show_with_short_id(self, tmp_path: Path) -> None:
        urls_file = _write_urls(tmp_path)
        db_path = tmp_path / "scan.db"
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/direct").mock(return_value=httpx.Response(200, text="ok"))
            mock.get("https://example.test/redirect").mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.test/direct"})
            )
            scan_result = runner.invoke(
                app,
                ["scan", str(urls_file), "--method", "GET", "--no-http2", "--database", str(db_path)],
            )
        assert scan_result.exit_code == 0
        scan_id = re.search(r"Starting scan (\S+)", scan_result.output).group(1)

        result = runner.invoke(app, ["show", scan_id[:8], "--database", str(db_path)])
        assert result.exit_code == 0, result.output

    def test_export_with_short_id(self, tmp_path: Path) -> None:
        urls_file = _write_urls(tmp_path)
        db_path = tmp_path / "scan.db"
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/direct").mock(return_value=httpx.Response(200, text="ok"))
            mock.get("https://example.test/redirect").mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.test/direct"})
            )
            scan_result = runner.invoke(
                app,
                ["scan", str(urls_file), "--method", "GET", "--no-http2", "--database", str(db_path)],
            )
        assert scan_result.exit_code == 0
        scan_id = re.search(r"Starting scan (\S+)", scan_result.output).group(1)

        output_csv = tmp_path / "out.csv"
        result = runner.invoke(app, ["export", scan_id[:8], str(output_csv), "--database", str(db_path)])
        assert result.exit_code == 0, result.output
        assert output_csv.exists()

    def test_ambiguous_short_id_errors_cleanly(self, tmp_path: Path) -> None:
        from redirecthunter.database import Database
        from redirecthunter.models import HTTPMethod, InputFormat, ScanConfig

        db_path = tmp_path / "scan.db"

        async def setup() -> str:
            db = Database(db_path)
            await db.connect()
            config1 = ScanConfig(input_path=tmp_path / "u.txt", input_format=InputFormat.TXT, method=HTTPMethod.GET)
            await db.create_scan(config1, total_urls=1)
            config2 = config1.model_copy(
                update={"scan_id": config1.scan_id[:4] + "0000-0000-0000-000000000000"}
            )
            await db.create_scan(config2, total_urls=1)
            await db.close()
            return config1.scan_id[:4]

        prefix = asyncio.run(setup())
        result = runner.invoke(app, ["stats", prefix, "--database", str(db_path)])
        assert result.exit_code == 1
        assert "matches" in result.output.lower() or "ambiguous" in result.output.lower()


class TestDeleteCommand:
    def _setup_two_scans(self, tmp_path: Path) -> tuple[Path, str, str]:
        from redirecthunter.database import Database
        from redirecthunter.models import HTTPMethod, InputFormat, RedirectResult, ScanConfig

        db_path = tmp_path / "scan.db"

        async def setup() -> tuple[str, str]:
            db = Database(db_path)
            await db.connect()
            ids = []
            for label, n in [("scan-A", 5), ("scan-B", 3)]:
                config = ScanConfig(
                    input_path=tmp_path / f"{label}.txt", input_format=InputFormat.TXT, scan_label=label
                )
                await db.create_scan(config, total_urls=n)
                for i in range(n):
                    await db.save_result(
                        RedirectResult(
                            scan_id=config.scan_id,
                            source_url=f"https://x.com/{label}/{i}",
                            expanded_url=f"https://x.com/{label}/{i}",
                            http_method=HTTPMethod.GET,
                            status_code=200,
                            alive=True,
                            latency_ms=10.0,
                        )
                    )
                ids.append(config.scan_id)
            await db.close()
            return ids[0], ids[1]

        scan_a, scan_b = asyncio.run(setup())
        return db_path, scan_a, scan_b

    def test_delete_cancelled_leaves_scan_intact(self, tmp_path: Path) -> None:
        db_path, scan_a, scan_b = self._setup_two_scans(tmp_path)
        result = runner.invoke(app, ["delete", scan_a[:8], "--database", str(db_path)], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output

        stats_result = runner.invoke(app, ["stats", "--database", str(db_path)])
        assert scan_a[:8] in stats_result.output  # still present

    def test_delete_with_yes_removes_only_target_scan(self, tmp_path: Path) -> None:
        db_path, scan_a, scan_b = self._setup_two_scans(tmp_path)
        result = runner.invoke(app, ["delete", scan_a[:8], "--database", str(db_path), "--yes"])
        assert result.exit_code == 0, result.output
        assert "Deleted scan" in result.output
        assert "5 results removed" in result.output

        # scan-A gone, scan-B untouched
        show_a = runner.invoke(app, ["show", scan_a[:8], "--database", str(db_path)])
        assert show_a.exit_code == 1

        show_b = runner.invoke(app, ["show", scan_b[:8], "--database", str(db_path)])
        assert show_b.exit_code == 0
        assert "Showing 3 result(s)" in show_b.output

    def test_delete_with_vacuum_flag(self, tmp_path: Path) -> None:
        db_path, scan_a, scan_b = self._setup_two_scans(tmp_path)
        result = runner.invoke(app, ["delete", scan_a[:8], "--database", str(db_path), "--yes", "--vacuum"])
        assert result.exit_code == 0, result.output
        assert "Disk space reclaimed" in result.output

    def test_delete_nonexistent_scan(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        _init_empty_db(db_path)
        result = runner.invoke(app, ["delete", "nonexistent-id", "--database", str(db_path), "--yes"])
        assert result.exit_code == 1

    def test_delete_missing_database(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["delete", "some-id", "--database", str(tmp_path / "nope.db"), "--yes"]
        )
        assert result.exit_code == 1


class TestVacuumCommand:
    def test_vacuum_runs_successfully(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        _init_empty_db(db_path)
        result = runner.invoke(app, ["vacuum", "--database", str(db_path)])
        assert result.exit_code == 0, result.output
        assert "Done." in result.output

    def test_vacuum_missing_database(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["vacuum", "--database", str(tmp_path / "nope.db")])
        assert result.exit_code == 1


class TestBacklinkCheckCommands:
    def _write_backlink_urls(self, tmp_path: Path) -> Path:
        f = tmp_path / "backlinks.txt"
        f.write_text(
            "https://source.test/has-link\n"
            "https://source.test/no-link\n"
            "https://source.test/text-only\n"
        )
        return f

    def test_bl_check_runs_and_persists(self, tmp_path: Path) -> None:
        urls_file = self._write_backlink_urls(tmp_path)
        db_path = tmp_path / "bl.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://source.test/has-link").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<html><body><a href="https://medilana.id/x">link</a></body></html>',
                )
            )
            mock.get("https://source.test/no-link").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>nothing</body></html>")
            )
            mock.get("https://source.test/text-only").mock(
                return_value=httpx.Response(
                    200, headers={"Content-Type": "text/html"}, text="<html><body>see medilana.id</body></html>"
                )
            )
            result = runner.invoke(
                app,
                [
                    "bl-check",
                    str(urls_file),
                    "-d",
                    "medilana.id",
                    "-c",
                    "2",
                    "--database",
                    str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Backlink Check Summary" in result.output
        assert db_path.exists()

    def test_bl_check_empty_input_is_clean_noop(self, tmp_path: Path) -> None:
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        result = runner.invoke(app, ["bl-check", str(empty_file), "-d", "medilana.id"])
        assert result.exit_code == 0
        assert "Nothing to do" in result.output

    def test_bl_check_headed_without_browser_is_rejected(self, tmp_path: Path) -> None:
        urls_file = self._write_backlink_urls(tmp_path)
        result = runner.invoke(
            app,
            ["bl-check", str(urls_file), "-d", "medilana.id", "--headed", "--database", str(tmp_path / "bl.db")],
        )
        assert result.exit_code == 1
        assert "--headed only makes sense with --browser" in result.output

    def test_bl_check_domain_from_config_file(self, tmp_path: Path) -> None:
        urls_file = self._write_backlink_urls(tmp_path)
        db_path = tmp_path / "bl.db"
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("bl_check:\n  domain: medilana.id\n")

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://source.test/has-link").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<html><body><a href="https://medilana.id/x">link</a></body></html>',
                )
            )
            mock.get("https://source.test/no-link").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>nothing</body></html>")
            )
            mock.get("https://source.test/text-only").mock(
                return_value=httpx.Response(
                    200, headers={"Content-Type": "text/html"}, text="<html><body>see medilana.id</body></html>"
                )
            )
            # No -d/--domain on the CLI -- domain must come from the
            # config file's bl_check: section.
            result = runner.invoke(
                app,
                [
                    "bl-check",
                    str(urls_file),
                    "--config",
                    str(config_file),
                    "--database",
                    str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "domain=medilana.id" in result.output

    def test_bl_check_cli_domain_overrides_config_file(self, tmp_path: Path) -> None:
        urls_file = self._write_backlink_urls(tmp_path)
        db_path = tmp_path / "bl.db"
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("bl_check:\n  domain: from-yaml.id\n")

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://source.test/has-link").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>nothing</body></html>")
            )
            mock.get("https://source.test/no-link").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>nothing</body></html>")
            )
            mock.get("https://source.test/text-only").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>nothing</body></html>")
            )
            result = runner.invoke(
                app,
                [
                    "bl-check",
                    str(urls_file),
                    "-d",
                    "from-cli.id",
                    "--config",
                    str(config_file),
                    "--database",
                    str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "domain=from-cli.id" in result.output

    def test_bl_check_captures_target_rel_and_robots_columns(self, tmp_path: Path) -> None:
        """Regression test for the target=_blank / robots-tag feature request."""
        urls_file = tmp_path / "backlinks.txt"
        urls_file.write_text("https://source.test/has-link\n")
        db_path = tmp_path / "bl.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://source.test/has-link").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html", "X-Robots-Tag": "noarchive"},
                    text=(
                        '<html><head><meta name="robots" content="noindex, follow"></head>'
                        '<body><a href="https://medilana.id/x" rel="nofollow" target="_blank">'
                        "link</a></body></html>"
                    ),
                )
            )
            result = runner.invoke(
                app, ["bl-check", str(urls_file), "-d", "medilana.id", "--database", str(db_path)]
            )
        assert result.exit_code == 0, result.output

        backlink_id = re.search(r"backlink_id:\s*(\S+)", result.output)
        assert backlink_id is not None
        export_path = tmp_path / "export.csv"
        export_result = runner.invoke(
            app,
            [
                "bl-export", backlink_id.group(1), "--database", str(db_path),
                "--format", "csv", "--output", str(export_path),
            ],
        )
        assert export_result.exit_code == 0, export_result.output

        rows = export_path.read_text()
        assert "_blank" in rows
        assert "nofollow" in rows
        assert "noindex, follow" in rows
        assert "noarchive" in rows

    def test_bl_stats_and_show_and_export_full_workflow(self, tmp_path: Path) -> None:
        urls_file = self._write_backlink_urls(tmp_path)
        db_path = tmp_path / "bl.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://source.test/has-link").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<html><body><a href="https://medilana.id/x">link</a></body></html>',
                )
            )
            mock.get("https://source.test/no-link").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>nothing</body></html>")
            )
            mock.get("https://source.test/text-only").mock(
                return_value=httpx.Response(
                    200, headers={"Content-Type": "text/html"}, text="<html><body>see medilana.id</body></html>"
                )
            )
            check_result = runner.invoke(
                app,
                ["bl-check", str(urls_file), "-d", "medilana.id", "--database", str(db_path)],
            )
        assert check_result.exit_code == 0, check_result.output

        # extract backlink_id from output
        match = re.search(r"backlink_id:\s*(\S+)", check_result.output)
        assert match is not None
        backlink_id = match.group(1)

        stats_result = runner.invoke(app, ["bl-stats", backlink_id[:8], "--database", str(db_path)])
        assert stats_result.exit_code == 0, stats_result.output
        assert "Backlink Check Summary" in stats_result.output

        stats_list_result = runner.invoke(app, ["bl-stats", "--database", str(db_path)])
        assert stats_list_result.exit_code == 0, stats_list_result.output
        assert "medilana.id" in stats_list_result.output

        show_result = runner.invoke(app, ["bl-show", backlink_id[:8], "--database", str(db_path)])
        assert show_result.exit_code == 0, show_result.output
        assert "source.test" in show_result.output

        show_confirmed = runner.invoke(
            app, ["bl-show", backlink_id[:8], "--confirmed", "--database", str(db_path)]
        )
        assert show_confirmed.exit_code == 0, show_confirmed.output
        assert "has-link" in show_confirmed.output
        assert "no-link" not in show_confirmed.output

        export_path = tmp_path / "export.csv"
        export_result = runner.invoke(
            app, ["bl-export", backlink_id[:8], "-o", str(export_path), "--database", str(db_path)]
        )
        assert export_result.exit_code == 0, export_result.output
        assert export_path.exists()
        content = export_path.read_text()
        assert "source_url" in content
        assert "has-link" in content

        json_export_path = tmp_path / "export.json"
        json_export_result = runner.invoke(
            app,
            [
                "bl-export",
                backlink_id[:8],
                "-o",
                str(json_export_path),
                "-f",
                "json",
                "--database",
                str(db_path),
            ],
        )
        assert json_export_result.exit_code == 0, json_export_result.output
        payload = json.loads(json_export_path.read_text())
        assert len(payload) == 3
        assert {row["source_url"] for row in payload} == {
            "https://source.test/has-link",
            "https://source.test/no-link",
            "https://source.test/text-only",
        }

    def test_bl_stats_missing_database(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["bl-stats", "--database", str(tmp_path / "nope.db")])
        assert result.exit_code == 1
        assert "Database not found" in result.output

    def test_bl_show_unknown_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "bl.db"
        _init_empty_db(db_path)
        result = runner.invoke(app, ["bl-show", "nonexistent", "--database", str(db_path)])
        assert result.exit_code == 1

    def test_bl_check_accounts_file_valid_mixed_input(self, tmp_path: Path) -> None:
        urls_file = tmp_path / "backlinks.txt"
        urls_file.write_text(
            "account_001|https://source.test/private\nhttps://source.test/public\n"
        )
        accounts_file = tmp_path / "accounts.txt"
        accounts_file.write_text("account_001|Cookie: session=xxxxx\n")
        db_path = tmp_path / "bl.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://source.test/private").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html></html>")
            )
            mock.get("https://source.test/public").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html></html>")
            )
            result = runner.invoke(
                app,
                [
                    "bl-check",
                    str(urls_file),
                    "-d",
                    "medilana.id",
                    "--accounts-file",
                    str(accounts_file),
                    "--database",
                    str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output

    def test_bl_check_missing_account_reference_is_hard_error_no_requests(
        self, tmp_path: Path
    ) -> None:
        urls_file = tmp_path / "backlinks.txt"
        urls_file.write_text("account_999|https://source.test/private\n")
        accounts_file = tmp_path / "accounts.txt"
        accounts_file.write_text("account_001|Cookie: session=xxxxx\n")
        db_path = tmp_path / "bl.db"

        with respx.mock(assert_all_called=False) as mock:
            route = mock.get("https://source.test/private").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html></html>")
            )
            result = runner.invoke(
                app,
                [
                    "bl-check",
                    str(urls_file),
                    "-d",
                    "medilana.id",
                    "--accounts-file",
                    str(accounts_file),
                    "--database",
                    str(db_path),
                ],
            )

        assert result.exit_code == 1
        assert "account_999" in result.output
        assert route.call_count == 0


class TestBlChainCommand:
    """Smoke coverage for `bl-chain` at the CliRunner level -- full 2-/3-tier
    derivation coverage (default vs --require-confirmed-parent, per-row
    |target override winning) lives in tests/test_backlink_chain.py."""

    def test_bl_chain_runs_and_persists_a_chain_summary(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://tier1.test/page\n")
        tier2 = tmp_path / "tier2.txt"
        tier2.write_text("https://tier2.test/page\n")
        db_path = tmp_path / "chain.db"

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://tier1.test/page").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<html><body><a href="https://medilana.id/x">link</a></body></html>',
                )
            )
            mock.get("https://tier2.test/page").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<html><body><a href="https://tier1.test/other">link</a></body></html>',
                )
            )
            result = runner.invoke(
                app,
                [
                    "bl-chain", str(tier1), str(tier2),
                    "-d", "medilana.id",
                    "--database", str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Backlink Chain Summary" in result.output
        assert "chain_id:" in result.output
        assert db_path.exists()

    def test_bl_chain_domain_from_config_file(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://tier1.test/page\n")
        tier2 = tmp_path / "tier2.txt"
        tier2.write_text("https://tier2.test/page\n")
        db_path = tmp_path / "chain.db"
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("bl_chain:\n  domain: medilana.id\n")

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://tier1.test/page").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<html><body><a href="https://medilana.id/x">link</a></body></html>',
                )
            )
            mock.get("https://tier2.test/page").mock(
                return_value=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text='<html><body><a href="https://tier1.test/other">link</a></body></html>',
                )
            )
            # No -d/--domain on the CLI -- domain must come from the
            # config file's bl_chain: section.
            result = runner.invoke(
                app,
                [
                    "bl-chain", str(tier1), str(tier2),
                    "--config", str(config_file),
                    "--database", str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Backlink Chain Summary" in result.output
        assert db_path.exists()

    def test_bl_chain_cli_domain_overrides_config_file(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://tier1.test/page\n")
        tier2 = tmp_path / "tier2.txt"
        tier2.write_text("https://tier2.test/page\n")
        db_path = tmp_path / "chain.db"
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("bl_chain:\n  domain: from-yaml.id\n")

        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://tier1.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>nothing</body></html>")
            )
            mock.get("https://tier2.test/page").mock(
                return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><body>nothing</body></html>")
            )
            result = runner.invoke(
                app,
                [
                    "bl-chain", str(tier1), str(tier2),
                    "-d", "from-cli.id",
                    "--config", str(config_file),
                    "--database", str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Backlink Chain Summary" in result.output
        assert db_path.exists()
