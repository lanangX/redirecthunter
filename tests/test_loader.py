"""Tests for redirecthunter.loader."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from redirecthunter.loader import LoaderError, count_candidates, load_candidates
from redirecthunter.models import InputFormat, ScanConfig


class TestTxtLoader:
    def test_skips_comments_and_blank_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com\n# comment\n\nhttps://b.com\n   \nhttps://c.com\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        urls = [c.raw_url for c in load_candidates(config)]
        assert urls == ["https://a.com", "https://b.com", "https://c.com"]
        assert count_candidates(config) == 3


class TestTargetOverrideTxt:
    """``|target`` (single) and ``|target1;target2;...`` (multi) row overrides."""

    def test_no_override(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].raw_url == "https://a.com"
        assert "target" not in candidates[0].row_metadata

    def test_single_target_unchanged(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com|medilana.id\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].raw_url == "https://a.com"
        assert candidates[0].row_metadata["target"] == ("medilana.id",)

    def test_multi_target_split_on_semicolon(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com|medilana.co.id;form.medilana.com;img.medilana.my.id\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["target"] == (
            "medilana.co.id",
            "form.medilana.com",
            "img.medilana.my.id",
        )

    def test_multi_target_strips_whitespace_around_entries(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com|medilana.co.id ; form.medilana.com ;  img.medilana.my.id  \n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["target"] == (
            "medilana.co.id",
            "form.medilana.com",
            "img.medilana.my.id",
        )

    def test_multi_target_drops_empty_entries(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com|medilana.co.id;;form.medilana.com;\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["target"] == ("medilana.co.id", "form.medilana.com")

    def test_all_blank_targets_treated_as_no_override(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com|;;;\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].raw_url == "https://a.com"
        assert "target" not in candidates[0].row_metadata

    def test_bare_trailing_pipe_treated_as_no_override(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com|\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert "target" not in candidates[0].row_metadata


class TestAccountIdTxt:
    """``account_id|`` (leading) prefix for per-row session/header selection."""

    def test_legacy_line_has_no_account_id(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("https://a.com|target\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert "account_id" not in candidates[0].row_metadata

    def test_account_prefix_alone(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("account_001|https://a.com\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].raw_url == "https://a.com"
        assert candidates[0].row_metadata["account_id"] == "account_001"

    def test_account_prefix_with_target_override(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text("account_001|https://a.com|target1;target2\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].raw_url == "https://a.com"
        assert candidates[0].row_metadata["account_id"] == "account_001"
        assert candidates[0].row_metadata["target"] == ("target1", "target2")

    def test_two_accounts_same_domain(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text(
            "account_001|https://example.com/a\naccount_002|https://example.com/b\n"
        )
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].raw_url == "https://example.com/a"
        assert candidates[0].row_metadata["account_id"] == "account_001"
        assert candidates[1].raw_url == "https://example.com/b"
        assert candidates[1].row_metadata["account_id"] == "account_002"

    def test_public_line_between_account_lines_unaffected(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text(
            "account_001|https://a.com\nhttps://public.com\naccount_002|https://b.com\n"
        )
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["account_id"] == "account_001"
        assert "account_id" not in candidates[1].row_metadata
        assert candidates[1].raw_url == "https://public.com"
        assert candidates[2].row_metadata["account_id"] == "account_002"

    def test_same_account_reused_across_rows_is_consistent(self, tmp_path: Path) -> None:
        f = tmp_path / "u.txt"
        f.write_text(
            "account_057|https://a.com\naccount_057|https://b.com\naccount_057|https://c.com\n"
        )
        config = ScanConfig(input_path=f, input_format=InputFormat.TXT)
        candidates = list(load_candidates(config))
        assert all(c.row_metadata["account_id"] == "account_057" for c in candidates)


class TestAccountIdCsvJson:
    """Representative ``account_id`` coverage for CSV/JSON (TXT is covered exhaustively above)."""

    def test_csv_account_id_column(self, tmp_path: Path) -> None:
        f = tmp_path / "u.csv"
        f.write_text("url,account_id\nhttps://a.com,account_001\nhttps://b.com,\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.CSV, input_column="url")
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["account_id"] == "account_001"
        assert "account_id" not in candidates[1].row_metadata

    def test_csv_account_id_column_case_insensitive_header(self, tmp_path: Path) -> None:
        f = tmp_path / "u.csv"
        f.write_text("url,Account_ID\nhttps://a.com,account_001\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.CSV, input_column="url")
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["account_id"] == "account_001"

    def test_json_account_id_key(self, tmp_path: Path) -> None:
        f = tmp_path / "u.json"
        f.write_text(json.dumps([{"url": "https://a.com", "account_id": "account_001"}]))
        config = ScanConfig(input_path=f, input_format=InputFormat.JSON)
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["account_id"] == "account_001"

    def test_json_blank_account_id_key_dropped(self, tmp_path: Path) -> None:
        f = tmp_path / "u.json"
        f.write_text(json.dumps([{"url": "https://a.com", "account_id": "  "}]))
        config = ScanConfig(input_path=f, input_format=InputFormat.JSON)
        candidates = list(load_candidates(config))
        assert "account_id" not in candidates[0].row_metadata


class TestCsvLoader:
    def test_with_header(self, tmp_path: Path) -> None:
        f = tmp_path / "u.csv"
        f.write_text("url,notes\nhttps://a.com,first\nhttps://b.com,second\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.CSV, input_column="url")
        candidates = list(load_candidates(config))
        assert [c.raw_url for c in candidates] == ["https://a.com", "https://b.com"]
        assert candidates[0].row_metadata.get("notes") == "first"

    def test_headerless_single_column(self, tmp_path: Path) -> None:
        f = tmp_path / "u.csv"
        f.write_text("https://x.com\nhttps://y.com\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.CSV)
        urls = [c.raw_url for c in load_candidates(config)]
        assert urls == ["https://x.com", "https://y.com"]

    def test_multi_column_header_not_misdetected_as_data(self, tmp_path: Path) -> None:
        """Regression test: csv.Sniffer().has_header() was previously used and
        silently misclassified this exact shape of file (short, uniform,
        3-column) as headerless, treating the header row itself as a URL."""
        f = tmp_path / "u.csv"
        f.write_text(
            "url,campaign,owner\n"
            "https://a.com,spring-launch,marketing\n"
            "https://b.com,newsletter,marketing\n"
            "https://c.com,affiliate,partnerships\n"
            "https://d.com,social,social\n"
        )
        config = ScanConfig(input_path=f, input_format=InputFormat.CSV, input_column="url")
        candidates = list(load_candidates(config))
        assert len(candidates) == 4  # not 5 -- the header row must not be yielded as a URL
        assert "url" not in [c.raw_url for c in candidates]
        assert candidates[0].raw_url == "https://a.com"
        assert candidates[0].row_metadata == {"campaign": "spring-launch", "owner": "marketing"}


class TestCsvTargetOverride:
    def test_single_target_column(self, tmp_path: Path) -> None:
        f = tmp_path / "u.csv"
        f.write_text("url,target\nhttps://a.com,medilana.id\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.CSV, input_column="url")
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["target"] == ("medilana.id",)

    def test_multi_target_column_case_insensitive_header(self, tmp_path: Path) -> None:
        f = tmp_path / "u.csv"
        f.write_text("url,Target\nhttps://a.com,medilana.co.id;form.medilana.com\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.CSV, input_column="url")
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["target"] == ("medilana.co.id", "form.medilana.com")

    def test_no_target_column(self, tmp_path: Path) -> None:
        f = tmp_path / "u.csv"
        f.write_text("url,notes\nhttps://a.com,first\n")
        config = ScanConfig(input_path=f, input_format=InputFormat.CSV, input_column="url")
        candidates = list(load_candidates(config))
        assert "target" not in candidates[0].row_metadata


class TestJsonLoader:
    def test_string_array(self, tmp_path: Path) -> None:
        f = tmp_path / "u.json"
        f.write_text(json.dumps(["https://a.com", "https://b.com", ""]))
        config = ScanConfig(input_path=f, input_format=InputFormat.JSON)
        urls = [c.raw_url for c in load_candidates(config)]
        assert urls == ["https://a.com", "https://b.com"]

    def test_object_array_with_metadata(self, tmp_path: Path) -> None:
        f = tmp_path / "u.json"
        f.write_text(json.dumps([{"url": "https://a.com", "source": "campaign1"}, {"no_url": 1}]))
        config = ScanConfig(input_path=f, input_format=InputFormat.JSON)
        candidates = list(load_candidates(config))
        assert len(candidates) == 1
        assert candidates[0].row_metadata == {"source": "campaign1"}

    def test_non_array_top_level_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "u.json"
        f.write_text(json.dumps({"not": "an array"}))
        config = ScanConfig(input_path=f, input_format=InputFormat.JSON)
        with pytest.raises(LoaderError):
            list(load_candidates(config))


class TestJsonTargetOverride:
    def test_single_target_key(self, tmp_path: Path) -> None:
        f = tmp_path / "u.json"
        f.write_text(json.dumps([{"url": "https://a.com", "target": "medilana.id"}]))
        config = ScanConfig(input_path=f, input_format=InputFormat.JSON)
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["target"] == ("medilana.id",)

    def test_multi_target_key(self, tmp_path: Path) -> None:
        f = tmp_path / "u.json"
        f.write_text(
            json.dumps([{"url": "https://a.com", "target": "medilana.co.id;form.medilana.com"}])
        )
        config = ScanConfig(input_path=f, input_format=InputFormat.JSON)
        candidates = list(load_candidates(config))
        assert candidates[0].row_metadata["target"] == ("medilana.co.id", "form.medilana.com")

    def test_all_blank_target_key_dropped(self, tmp_path: Path) -> None:
        f = tmp_path / "u.json"
        f.write_text(json.dumps([{"url": "https://a.com", "target": ";;"}]))
        config = ScanConfig(input_path=f, input_format=InputFormat.JSON)
        candidates = list(load_candidates(config))
        assert "target" not in candidates[0].row_metadata


class TestSqliteLoader:
    def _make_db(self, path: Path) -> None:
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, tag TEXT)")
        conn.execute("INSERT INTO urls (url, tag) VALUES ('https://a.com', 'x')")
        conn.execute("INSERT INTO urls (url, tag) VALUES ('https://b.com', 'y')")
        conn.commit()
        conn.close()

    def test_reads_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "u.db"
        self._make_db(db_path)
        config = ScanConfig(input_path=db_path, input_format=InputFormat.SQLITE, input_table="urls", input_column="url")
        candidates = list(load_candidates(config))
        assert [c.raw_url for c in candidates] == ["https://a.com", "https://b.com"]
        assert candidates[0].row_metadata.get("tag") == "x"

    def test_invalid_table_identifier_rejected(self, tmp_path: Path) -> None:
        db_path = tmp_path / "u.db"
        self._make_db(db_path)
        config = ScanConfig(
            input_path=db_path, input_format=InputFormat.SQLITE, input_table="urls; DROP TABLE urls;--"
        )
        with pytest.raises(LoaderError):
            list(load_candidates(config))

    def test_missing_table_raises(self, tmp_path: Path) -> None:
        db_path = tmp_path / "u.db"
        self._make_db(db_path)
        config = ScanConfig(input_path=db_path, input_format=InputFormat.SQLITE, input_table="nonexistent")
        with pytest.raises(LoaderError):
            list(load_candidates(config))


class TestMissingFile:
    def test_missing_input_file_raises(self, tmp_path: Path) -> None:
        config = ScanConfig(input_path=tmp_path / "nope.txt", input_format=InputFormat.TXT)
        with pytest.raises(LoaderError):
            list(load_candidates(config))
