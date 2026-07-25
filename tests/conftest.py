"""Shared pytest fixtures for the RedirectHunter test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from redirecthunter.models import HTTPMethod, InputFormat, ScanConfig


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
