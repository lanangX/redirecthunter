"""Tests for redirecthunter.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from redirecthunter.config import (
    ConfigError,
    build_scan_config,
    discover_config_file,
    infer_input_format,
    load_yaml_config,
)
from redirecthunter.models import InputFormat


class TestInferInputFormat:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("urls.txt", InputFormat.TXT),
            ("urls.csv", InputFormat.CSV),
            ("urls.json", InputFormat.JSON),
            ("urls.db", InputFormat.SQLITE),
            ("urls.sqlite", InputFormat.SQLITE),
        ],
    )
    def test_known_extensions(self, tmp_path: Path, filename: str, expected: InputFormat) -> None:
        assert infer_input_format(tmp_path / filename) == expected

    def test_unknown_extension_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            infer_input_format(tmp_path / "urls.xyz")


class TestDiscoverConfigFile:
    def test_explicit_path_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            discover_config_file(tmp_path / "nonexistent.yaml")

    def test_explicit_path_returned_if_exists(self, tmp_path: Path) -> None:
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("workers: 50\n")
        assert discover_config_file(config_file) == config_file

    def test_auto_discovery(self, tmp_path: Path) -> None:
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("workers: 50\n")
        assert discover_config_file(None, search_dir=tmp_path) == config_file

    def test_no_config_file_returns_none(self, tmp_path: Path) -> None:
        assert discover_config_file(None, search_dir=tmp_path) is None


class TestLoadYamlConfig:
    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.yaml"
        f.write_text("")
        assert load_yaml_config(f) == {}

    def test_non_mapping_top_level_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "list.yaml"
        f.write_text("- a\n- b\n")
        with pytest.raises(ConfigError):
            load_yaml_config(f)

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text("workers: [unclosed\n")
        with pytest.raises(ConfigError):
            load_yaml_config(f)


class TestBuildScanConfig:
    def test_cli_overrides_yaml(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("workers: 50\ntimeout: 8\n")

        config = build_scan_config(input_path=input_file, workers=300, config_file=config_file)
        assert config.workers == 300  # CLI wins
        assert config.timeout == 8.0  # YAML value used since CLI didn't set it

    def test_yaml_only(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("workers: 77\nuser_agent: CustomAgent\n")

        config = build_scan_config(input_path=input_file, config_file=config_file)
        assert config.workers == 77
        assert config.user_agent == "CustomAgent"

    def test_extra_headers_merge_not_replace(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("extra_headers:\n  X-From-Yaml: yaml-value\n")

        config = build_scan_config(
            input_path=input_file, config_file=config_file, extra_headers={"X-From-Cli": "cli-value"}
        )
        assert config.extra_headers == {"X-From-Yaml": "yaml-value", "X-From-Cli": "cli-value"}

    def test_format_inferred_when_not_specified(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.csv"
        input_file.write_text("https://a.com\n")
        config = build_scan_config(input_path=input_file)
        assert config.input_format == InputFormat.CSV

    def test_invalid_merged_config_raises_configerror(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        with pytest.raises(ConfigError):
            build_scan_config(input_path=input_file, workers=99999)  # exceeds max
