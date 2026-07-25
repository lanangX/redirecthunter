"""Tests for redirecthunter.models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from redirecthunter.models import (
    HTTPMethod,
    InputFormat,
    RedirectResult,
    RedirectType,
    ScanConfig,
    ScanStatus,
    ScanSummary,
)


class TestScanConfig:
    def test_defaults(self, tmp_path: Path) -> None:
        config = ScanConfig(input_path=tmp_path / "urls.txt", input_format=InputFormat.TXT)
        assert config.method == HTTPMethod.HEAD
        assert config.workers == 100
        assert config.follow_redirects is True
        assert config.max_redirects == 10
        assert config.retry == 2
        assert config.http2 is True
        assert config.scan_id  # auto-generated UUID, non-empty

    def test_rejects_unknown_fields(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            ScanConfig(
                input_path=tmp_path / "urls.txt",
                input_format=InputFormat.TXT,
                not_a_real_field=123,
            )

    def test_workers_bounds_enforced(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            ScanConfig(input_path=tmp_path / "u.txt", input_format=InputFormat.TXT, workers=0)
        with pytest.raises(ValidationError):
            ScanConfig(input_path=tmp_path / "u.txt", input_format=InputFormat.TXT, workers=5000)

    def test_target_whitespace_normalized_to_none(self, tmp_path: Path) -> None:
        config = ScanConfig(input_path=tmp_path / "u.txt", input_format=InputFormat.TXT, target="   ")
        assert config.target is None

    def test_is_frozen(self, tmp_path: Path) -> None:
        config = ScanConfig(input_path=tmp_path / "u.txt", input_format=InputFormat.TXT)
        with pytest.raises(ValidationError):
            config.workers = 999  # type: ignore[misc]


class TestRedirectResult:
    def test_minimal_construction(self) -> None:
        result = RedirectResult(
            scan_id="s1",
            source_url="https://a.com",
            expanded_url="https://a.com",
            http_method=HTTPMethod.HEAD,
            alive=True,
            latency_ms=10.0,
        )
        assert result.redirect_type == RedirectType.NONE
        assert result.hop_count == 0
        assert result.redirect_chain == []
        assert result.result_id  # auto-generated

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RedirectResult(
                scan_id="s1",
                source_url="https://a.com",
                expanded_url="https://a.com",
                http_method=HTTPMethod.HEAD,
                alive=True,
                latency_ms=-1.0,
            )


class TestScanSummary:
    def test_progress_pct(self) -> None:
        summary = ScanSummary(scan_id="s1", status=ScanStatus.RUNNING, total_urls=200, completed=50)
        assert summary.progress_pct == 25.0

    def test_progress_pct_zero_total(self) -> None:
        summary = ScanSummary(scan_id="s1", status=ScanStatus.RUNNING, total_urls=0, completed=0)
        assert summary.progress_pct == 0.0
