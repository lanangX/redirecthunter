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
