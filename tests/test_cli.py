"""Tests for redirecthunter.cli, using Typer's CliRunner + respx (no real sockets)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from redirecthunter.cli import app
from redirecthunter.database import Database
from redirecthunter.models import HTTPMethod, InputFormat, RedirectResult, ScanConfig, ScanStatus

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
            await db.update_scan_status(config.scan_id, ScanStatus.INTERRUPTED)
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

    def test_find_output_include_source(self, tmp_path: Path) -> None:
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
                "--include-source",
            ],
        )
        assert result.exit_code == 0, result.output
        content = output_file.read_text()
        assert "https://example.test/ext -> https://evil-tracker.com/steal" in content

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
