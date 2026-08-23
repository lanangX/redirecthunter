"""Tests for redirecthunter.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from redirecthunter.config import (
    ConfigError,
    build_backlink_chain_config,
    build_backlink_check_config,
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


class TestBuildBacklinkCheckConfig:
    def test_domain_missing_from_both_raises(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        with pytest.raises(ConfigError, match="Missing target domain"):
            build_backlink_check_config(input_path=input_file)

    def test_domain_from_yaml_only(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("bl_check:\n  domain: medilana.id\n")

        config, accounts_file = build_backlink_check_config(input_path=input_file, config_file=config_file)
        assert config.domain == "medilana.id"
        assert accounts_file is None

    def test_cli_overrides_yaml(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("bl_check:\n  domain: medilana.id\n  concurrency: 4\n")

        config, _ = build_backlink_check_config(
            input_path=input_file, domain="medilana.id", concurrency=16, config_file=config_file
        )
        assert config.concurrency == 16  # CLI wins over YAML's 4

    def test_accounts_file_not_found_raises(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        with pytest.raises(ConfigError, match="--accounts-file not found"):
            build_backlink_check_config(
                input_path=input_file,
                domain="medilana.id",
                accounts_file=tmp_path / "nonexistent-accounts.txt",
            )

    def test_accounts_file_resolved_from_cli(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        accounts_file = tmp_path / "accounts.txt"
        accounts_file.write_text("acct1|Cookie: session=abc\n")

        _, resolved = build_backlink_check_config(
            input_path=input_file, domain="medilana.id", accounts_file=accounts_file
        )
        assert resolved == accounts_file

    def test_accounts_file_resolved_from_yaml(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        accounts_file = tmp_path / "accounts.txt"
        accounts_file.write_text("acct1|Cookie: session=abc\n")
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text(f"bl_check:\n  domain: medilana.id\n  accounts_file: {accounts_file}\n")

        _, resolved = build_backlink_check_config(input_path=input_file, config_file=config_file)
        assert resolved == accounts_file

    def test_exact_and_strict_true(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config, _ = build_backlink_check_config(
            input_path=input_file, domain="medilana.id", exact=True, strict=True
        )
        assert config.allow_subdomains is False
        assert config.check_indirect is False

    def test_exact_and_strict_default_false(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config, _ = build_backlink_check_config(input_path=input_file, domain="medilana.id")
        assert config.allow_subdomains is True
        assert config.check_indirect is True

    def test_concurrency_default_httpx_mode(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config, _ = build_backlink_check_config(input_path=input_file, domain="medilana.id")
        assert config.concurrency == 8

    def test_concurrency_default_browser_mode(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        config, _ = build_backlink_check_config(input_path=input_file, domain="medilana.id", browser=True)
        assert config.concurrency == 4

    def test_returns_tuple_of_config_and_path(self, tmp_path: Path) -> None:
        input_file = tmp_path / "urls.txt"
        input_file.write_text("https://a.com\n")
        result = build_backlink_check_config(input_path=input_file, domain="medilana.id")
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestBuildBacklinkChainConfig:
    def test_domain_missing_from_both_raises(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        with pytest.raises(ConfigError, match="Missing root target domain"):
            build_backlink_chain_config(tier_paths=[tier1])

    def test_domain_from_yaml_only(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("bl_chain:\n  domain: medilana.id\n")

        config, accounts_file = build_backlink_chain_config(tier_paths=[tier1], config_file=config_file)
        assert config.domain == "medilana.id"
        assert accounts_file is None

    def test_cli_overrides_yaml(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        config_file = tmp_path / "redirecthunter.yaml"
        config_file.write_text("bl_chain:\n  domain: medilana.id\n  concurrency: 4\n")

        config, _ = build_backlink_chain_config(
            tier_paths=[tier1], domain="medilana.id", concurrency=16, config_file=config_file
        )
        assert config.concurrency == 16  # CLI wins over YAML's 4

    def test_accounts_file_not_found_raises(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        with pytest.raises(ConfigError, match="--accounts-file not found"):
            build_backlink_chain_config(
                tier_paths=[tier1],
                domain="medilana.id",
                accounts_file=tmp_path / "nonexistent-accounts.txt",
            )

    def test_exact_and_strict_true(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        config, _ = build_backlink_chain_config(
            tier_paths=[tier1], domain="medilana.id", exact=True, strict=True
        )
        assert config.allow_subdomains is False
        assert config.check_indirect is False

    def test_exact_and_strict_default_false(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        config, _ = build_backlink_chain_config(tier_paths=[tier1], domain="medilana.id")
        assert config.allow_subdomains is True
        assert config.check_indirect is True

    def test_concurrency_default_httpx_mode(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        config, _ = build_backlink_chain_config(tier_paths=[tier1], domain="medilana.id")
        assert config.concurrency == 8

    def test_concurrency_default_browser_mode(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        config, _ = build_backlink_chain_config(tier_paths=[tier1], domain="medilana.id", browser=True)
        assert config.concurrency == 4

    def test_tier_paths_always_from_cli_not_yaml(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        tier2 = tmp_path / "tier2.txt"
        tier2.write_text("https://b.com\n")
        config_file = tmp_path / "redirecthunter.yaml"
        # bl_chain: has no tier_paths field at all -- even if someone tries
        # to sneak one in via YAML it must be ignored, since tier_paths is
        # not a recognized key and would fail model validation if it leaked
        # through unfiltered.
        config_file.write_text("bl_chain:\n  domain: medilana.id\n")

        config, _ = build_backlink_chain_config(
            tier_paths=[tier1, tier2], config_file=config_file
        )
        assert config.tier_paths == [tier1, tier2]

    def test_returns_tuple_of_config_and_path(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://a.com\n")
        result = build_backlink_chain_config(tier_paths=[tier1], domain="medilana.id")
        assert isinstance(result, tuple)
        assert len(result) == 2
