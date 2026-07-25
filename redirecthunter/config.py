"""Configuration resolution for RedirectHunter.

Configuration is layered with the following priority (highest wins):

    1. Explicit CLI flags        (passed by ``cli.py``)
    2. YAML config file          (``--config`` or auto-discovered)
    3. Built-in defaults         (``ScanConfig`` field defaults)

No other module reads raw YAML or raw CLI arguments directly — everything
downstream (engine, detector, database, exporter) consumes a fully-resolved,
validated :class:`~redirecthunter.models.ScanConfig` instance produced here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from redirecthunter.models import HTTPMethod, InputFormat, ScanConfig

#: Filenames auto-discovered in the current working directory when the
#: caller does not explicitly pass ``--config``.
DEFAULT_CONFIG_FILENAMES: tuple[str, ...] = (
    "redirecthunter.yaml",
    "redirecthunter.yml",
    ".redirecthunter.yaml",
    ".redirecthunter.yml",
)

#: Maps a file extension to its corresponding InputFormat.
_EXTENSION_FORMAT_MAP: dict[str, InputFormat] = {
    ".txt": InputFormat.TXT,
    ".csv": InputFormat.CSV,
    ".json": InputFormat.JSON,
    ".db": InputFormat.SQLITE,
    ".sqlite": InputFormat.SQLITE,
    ".sqlite3": InputFormat.SQLITE,
}


class ConfigError(Exception):
    """Raised when configuration cannot be loaded, parsed, or validated."""


def discover_config_file(explicit_path: Path | None, search_dir: Path | None = None) -> Path | None:
    """Resolve which YAML config file (if any) should be loaded.

    Args:
        explicit_path: Path passed via ``--config``. If provided and it does
            not exist, this is treated as a hard error (the user asked for a
            specific file).
        search_dir: Directory to search for auto-discovered config files.
            Defaults to the current working directory.

    Returns:
        The resolved config file path, or ``None`` if no config file should
        be loaded.

    Raises:
        ConfigError: If ``explicit_path`` was given but does not exist.
    """
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise ConfigError(f"Config file not found: {explicit_path}")
        return explicit_path

    directory = search_dir or Path.cwd()
    for filename in DEFAULT_CONFIG_FILENAMES:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def load_yaml_config(path: Path) -> dict[str, Any]:
    """Load and parse a YAML config file into a plain dict.

    An empty file yields ``{}`` rather than ``None``, so callers never need
    to guard against a null return value.

    Raises:
        ConfigError: On I/O failure or malformed YAML.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a YAML mapping at the top level.")
    return data


def infer_input_format(input_path: Path) -> InputFormat:
    """Infer the candidate-URL input format from a file extension.

    Raises:
        ConfigError: If the extension is not recognized and no explicit
            format was supplied by the caller.
    """
    suffix = input_path.suffix.lower()
    fmt = _EXTENSION_FORMAT_MAP.get(suffix)
    if fmt is None:
        supported = ", ".join(sorted(_EXTENSION_FORMAT_MAP))
        raise ConfigError(
            f"Cannot infer input format from extension '{suffix}' for {input_path}. "
            f"Supported extensions: {supported}. Pass --format explicitly to override."
        )
    return fmt


def _strip_none(values: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None so they don't shadow lower-priority layers."""
    return {k: v for k, v in values.items() if v is not None}


def build_scan_config(
    *,
    input_path: Path,
    input_format: InputFormat | None = None,
    input_table: str | None = None,
    input_column: str | None = None,
    target: str | None = None,
    method: HTTPMethod | None = None,
    follow_redirects: bool | None = None,
    max_redirects: int | None = None,
    workers: int | None = None,
    timeout: float | None = None,
    connect_timeout: float | None = None,
    retry: int | None = None,
    retry_backoff: float | None = None,
    rate_limit: float | None = None,
    http2: bool | None = None,
    proxy: str | None = None,
    user_agent: str | None = None,
    extra_headers: dict[str, str] | None = None,
    verify_tls: bool | None = None,
    database_path: Path | None = None,
    scan_id: str | None = None,
    scan_label: str | None = None,
    config_file: Path | None = None,
) -> ScanConfig:
    """Resolve a fully-validated :class:`ScanConfig` from layered sources.

    All keyword arguments except ``input_path`` and ``config_file`` are
    optional and represent CLI-flag overrides. A value of ``None`` means
    "not explicitly set on the CLI" and allows the YAML file (or the
    ``ScanConfig`` default) to take effect instead.

    Args:
        input_path: Path to the candidate-URL input file. Always required
            and always sourced from the CLI positional argument.
        input_format: Explicit input format; inferred from extension if
            omitted entirely (CLI *and* YAML both silent).
        config_file: Explicit ``--config`` path, or ``None`` to auto-discover.
        (remaining args mirror :class:`~redirecthunter.models.ScanConfig` fields)

    Returns:
        A validated, immutable ``ScanConfig``.

    Raises:
        ConfigError: If the config file is invalid or the merged
            configuration fails Pydantic validation.
    """
    resolved_config_path = discover_config_file(config_file)
    yaml_data: dict[str, Any] = {}
    if resolved_config_path is not None:
        yaml_data = load_yaml_config(resolved_config_path)

    # Layer 1: YAML values (already lowest of the two override layers).
    merged: dict[str, Any] = dict(yaml_data)

    # Layer 2: CLI overrides — only keys the caller actually set.
    cli_overrides = _strip_none(
        {
            "input_table": input_table,
            "input_column": input_column,
            "target": target,
            "method": method,
            "follow_redirects": follow_redirects,
            "max_redirects": max_redirects,
            "workers": workers,
            "timeout": timeout,
            "connect_timeout": connect_timeout,
            "retry": retry,
            "retry_backoff": retry_backoff,
            "rate_limit": rate_limit,
            "http2": http2,
            "proxy": proxy,
            "user_agent": user_agent,
            "extra_headers": extra_headers,
            "verify_tls": verify_tls,
            "database_path": database_path,
            "scan_id": scan_id,
            "scan_label": scan_label,
        }
    )
    merged.update(cli_overrides)

    # extra_headers merges (rather than replaces) YAML + CLI instead of one
    # fully shadowing the other, since operators commonly want both a
    # standing YAML header set plus one ad-hoc CLI addition.
    if "extra_headers" in yaml_data and extra_headers:
        merged["extra_headers"] = {**yaml_data["extra_headers"], **extra_headers}

    resolved_format = input_format or merged.pop("input_format", None)
    if resolved_format is None:
        resolved_format = infer_input_format(input_path)

    merged["input_path"] = input_path
    merged["input_format"] = resolved_format

    try:
        return ScanConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration:\n{exc}") from exc


__all__ = [
    "ConfigError",
    "DEFAULT_CONFIG_FILENAMES",
    "discover_config_file",
    "load_yaml_config",
    "infer_input_format",
    "build_scan_config",
]
