"""Tests for target-replace functionality: redact_domain() (utils.py) plus
the redact-target/expand-target CLI commands and their supporting I/O
module (redirecthunter/target_replace.py).
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from redirecthunter.cli import app
from redirecthunter.utils import TARGET_PLACEHOLDER, redact_domain

runner = CliRunner(env={"COLUMNS": "220", "TERM": "xterm-256color"})


# ---------------------------------------------------------------------------
# redact_domain() — pure function tests
# ---------------------------------------------------------------------------


class TestRedactDomain:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Bare domain, no scheme.
            ("medilana.id", "{TARGET}"),
            ("medilana.id/", "{TARGET}"),
            # www. prefix.
            ("www.medilana.id", "{TARGET}"),
            # http/https, plain.
            ("http://medilana.id", "{TARGET}"),
            ("https://medilana.id", "{TARGET}"),
            ("https://medilana.id/", "{TARGET}"),
            ("https://www.medilana.id/", "{TARGET}"),
            # Percent-encoded scheme variants.
            ("http%3A%2F%2Fmedilana.id", "{TARGET}"),
            ("http%3A%2Fmedilana.id", "{TARGET}"),
            ("http%3A//medilana.id", "{TARGET}"),
            # Doubled scheme+www prefix (bad-data concatenation artifact).
            ("http://www.https://medilana.id/", "{TARGET}"),
            # Trailing slashes, plain and encoded, multiple.
            ("https://medilana.id///", "{TARGET}"),
            ("https://medilana.id%2F", "{TARGET}"),
            # In context, inside a full redirect-parameter URL.
            (
                "https://redir.example/go?u=https://medilana.id/page",
                "https://redir.example/go?u={TARGET}/page",
            ),
        ],
    )
    def test_matches_and_replaces(self, raw: str, expected: str) -> None:
        assert redact_domain(raw, "medilana.id") == expected

    def test_custom_token(self) -> None:
        assert redact_domain("https://medilana.id/", "medilana.id", token="[X]") == "[X]"

    def test_default_token_is_target_placeholder(self) -> None:
        assert redact_domain("https://medilana.id/", "medilana.id") == TARGET_PLACEHOLDER

    def test_no_match_returns_unchanged(self) -> None:
        line = "https://totally-unrelated.example/x"
        assert redact_domain(line, "medilana.id") == line

    def test_multiple_occurrences_all_replaced(self) -> None:
        line = "https://medilana.id/a https://medilana.id/b"
        assert redact_domain(line, "medilana.id") == "{TARGET}/a {TARGET}/b"

    # -- Left/right boundary rejection (substring-of-different-domain) -----

    @pytest.mark.parametrize(
        "raw",
        [
            "https://tmedilana.id/",
            "https://sysmedilana.id/",
        ],
    )
    def test_left_boundary_rejects_longer_domain_suffix(self, raw: str) -> None:
        assert redact_domain(raw, "medilana.id") == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "https://medilana.id.cheapdealuk.co.uk/",
            "https://medilana.idmanagement.html",
        ],
    )
    def test_right_boundary_rejects_longer_domain_prefix(self, raw: str) -> None:
        assert redact_domain(raw, "medilana.id") == raw

    # -- Three documented, intentional known limitations -------------------

    def test_known_limitation_partially_broken_percent_encoding(self) -> None:
        """A truncated '%2F' (missing the trailing 'F') isn't recognized as a scheme."""
        line = "http%3A%2medilana.id"
        assert redact_domain(line, "medilana.id") == line

    def test_known_limitation_two_urls_concatenated_no_separator(self) -> None:
        """Two URLs glued together with no separator aren't replaced at the boundary."""
        line = "https://medilana.idhttp://other.com/"
        assert redact_domain(line, "medilana.id") == line

    def test_known_limitation_encoded_site_operator_glued_to_domain(self) -> None:
        """An encoded Google site: operator glued to the domain may fail the left boundary."""
        line = "site%3Amedilana.id"
        assert redact_domain(line, "medilana.id") == line


# ---------------------------------------------------------------------------
# `redact-target` CLI command
# ---------------------------------------------------------------------------


def _write_lines(tmp_path: Path, name: str, lines: list[str]) -> Path:
    f = tmp_path / name
    f.write_text("\n".join(lines) + "\n")
    return f


class TestRedactTargetCommand:
    def test_txt_output_default_stdout(self, tmp_path: Path) -> None:
        input_file = _write_lines(
            tmp_path,
            "urls.txt",
            ["https://medilana.id/a", "https://unrelated.example/b"],
        )
        result = runner.invoke(app, ["redact-target", str(input_file), "-d", "medilana.id"])
        assert result.exit_code == 0, result.output
        assert "{TARGET}/a" in result.output
        assert "https://unrelated.example/b" in result.output

    def test_txt_output_to_file(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "urls.txt", ["https://medilana.id/a"])
        output_file = tmp_path / "out.txt"
        result = runner.invoke(
            app, ["redact-target", str(input_file), "--domain", "medilana.id", "-o", str(output_file)]
        )
        assert result.exit_code == 0, result.output
        assert output_file.read_text().strip() == "{TARGET}/a"

    def test_csv_output(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "urls.txt", ["https://medilana.id/a"])
        output_file = tmp_path / "out.csv"
        result = runner.invoke(
            app, ["redact-target", str(input_file), "-d", "medilana.id", "-o", str(output_file)]
        )
        assert result.exit_code == 0, result.output
        with output_file.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[0] == ["target_url", "original_url"]
        assert rows[1] == ["{TARGET}/a", "https://medilana.id/a"]

    def test_csv_format_explicit_overrides_extension(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "urls.txt", ["https://medilana.id/a"])
        output_file = tmp_path / "out.txt"
        result = runner.invoke(
            app,
            ["redact-target", str(input_file), "-d", "medilana.id", "-o", str(output_file), "-f", "csv"],
        )
        assert result.exit_code == 0, result.output
        assert "target_url,original_url" in output_file.read_text()

    def test_json_output(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "urls.txt", ["https://medilana.id/a"])
        output_file = tmp_path / "out.json"
        result = runner.invoke(
            app, ["redact-target", str(input_file), "-d", "medilana.id", "-o", str(output_file)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(output_file.read_text())
        assert data == [{"target_url": "{TARGET}/a", "original_url": "https://medilana.id/a"}]

    def test_sqlite_output(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "urls.txt", ["https://medilana.id/a"])
        output_file = tmp_path / "out.db"
        result = runner.invoke(
            app, ["redact-target", str(input_file), "-d", "medilana.id", "-o", str(output_file)]
        )
        assert result.exit_code == 0, result.output
        conn = sqlite3.connect(output_file)
        try:
            rows = conn.execute("SELECT target_url, original_url FROM urls").fetchall()
        finally:
            conn.close()
        assert rows == [("{TARGET}/a", "https://medilana.id/a")]

    def test_sqlite_format_requires_explicit_output_path(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "urls.txt", ["https://medilana.id/a"])
        result = runner.invoke(app, ["redact-target", str(input_file), "-d", "medilana.id", "-f", "sqlite"])
        assert result.exit_code != 0

    def test_verbose_reports_unmatched_lines_and_total(self, tmp_path: Path) -> None:
        input_file = _write_lines(
            tmp_path, "urls.txt", ["https://medilana.id/a", "https://unrelated.example/b"]
        )
        result = runner.invoke(app, ["redact-target", str(input_file), "-d", "medilana.id", "-v"])
        assert result.exit_code == 0, result.output
        assert "https://unrelated.example/b" in result.stderr
        assert "1" in result.stderr

    def test_unmatched_lines_passthrough_by_default(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "urls.txt", ["https://unrelated.example/b"])
        result = runner.invoke(app, ["redact-target", str(input_file), "-d", "medilana.id"])
        assert result.exit_code == 0, result.output
        assert "https://unrelated.example/b" in result.output

    def test_custom_token(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "urls.txt", ["https://medilana.id/a"])
        result = runner.invoke(
            app, ["redact-target", str(input_file), "-d", "medilana.id", "-t", "[X]"]
        )
        assert result.exit_code == 0, result.output
        assert "[X]/a" in result.output


# ---------------------------------------------------------------------------
# `expand-target` CLI command
# ---------------------------------------------------------------------------


class TestExpandTargetCommand:
    def test_basic_expansion(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "templates.txt", ["https://redir.example/go?u={TARGET}"])
        result = runner.invoke(
            app, ["expand-target", str(input_file), "--target", "https://example.org"]
        )
        assert result.exit_code == 0, result.output
        assert "https://redir.example/go?u=https://example.org" in result.output

    def test_encode_flag_percent_encodes_target(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "templates.txt", ["https://redir.example/go?u={TARGET}"])
        result = runner.invoke(
            app,
            ["expand-target", str(input_file), "--target", "https://example.org?x=1", "--encode"],
        )
        assert result.exit_code == 0, result.output
        assert "https%3A%2F%2Fexample.org%3Fx%3D1" in result.output

    def test_passthrough_of_untemplated_lines(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "templates.txt", ["https://plain.example/no-template"])
        result = runner.invoke(
            app, ["expand-target", str(input_file), "--target", "https://example.org"]
        )
        assert result.exit_code == 0, result.output
        assert "https://plain.example/no-template" in result.output

    def test_verbose_reports_untemplated_lines_and_total(self, tmp_path: Path) -> None:
        input_file = _write_lines(
            tmp_path,
            "templates.txt",
            ["https://redir.example/go?u={TARGET}", "https://plain.example/no-template"],
        )
        result = runner.invoke(
            app, ["expand-target", str(input_file), "--target", "https://example.org", "-v"]
        )
        assert result.exit_code == 0, result.output
        assert "https://plain.example/no-template" in result.stderr
        assert "1" in result.stderr

    def test_output_to_file(self, tmp_path: Path) -> None:
        input_file = _write_lines(tmp_path, "templates.txt", ["https://redir.example/go?u={TARGET}"])
        output_file = tmp_path / "out.txt"
        result = runner.invoke(
            app,
            [
                "expand-target",
                str(input_file),
                "--target",
                "https://example.org",
                "-o",
                str(output_file),
            ],
        )
        assert result.exit_code == 0, result.output
        assert output_file.read_text().strip() == "https://redir.example/go?u=https://example.org"
