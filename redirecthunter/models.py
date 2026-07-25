"""Core data contracts for RedirectHunter.

Every other module in this package (engine, detector, analyzer, database,
exporter, cli) consumes or produces the models defined here. Keeping them
centralized in one Pydantic-validated module guarantees a single source of
truth for the shape of a scan, a redirect hop, and a final result — no
module is allowed to invent its own ad-hoc dict shape for this data.

All models are immutable-by-convention (validated on construction) and use
Pydantic v2 syntax throughout.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp.

    Centralized so every model uses the exact same clock semantics
    (aware, UTC) — avoids naive-vs-aware datetime bugs when persisting
    to SQLite or serializing to JSON.
    """
    return datetime.now(UTC)


class HTTPMethod(str, Enum):
    """HTTP methods supported by the scanning engine."""

    HEAD = "HEAD"
    GET = "GET"


class RedirectType(str, Enum):
    """Classification of how a redirect was expressed.

    HTTP status-code redirects and content-based redirects (meta refresh,
    JavaScript) are distinguished because they require different detection
    strategies and carry different security implications.
    """

    HTTP_301 = "301_moved_permanently"
    HTTP_302 = "302_found"
    HTTP_303 = "303_see_other"
    HTTP_307 = "307_temporary_redirect"
    HTTP_308 = "308_permanent_redirect"
    META_REFRESH = "meta_refresh"
    JAVASCRIPT = "javascript"
    NONE = "none"


class InputFormat(str, Enum):
    """Supported candidate-URL input file formats."""

    TXT = "txt"
    CSV = "csv"
    JSON = "json"
    SQLITE = "sqlite"


class ExportFormat(str, Enum):
    """Supported result export formats."""

    CSV = "csv"
    JSON = "json"
    SQLITE = "sqlite"


class ScanStatus(str, Enum):
    """Lifecycle status of a scan record."""

    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class CloudflareStatus(BaseModel):
    """Classification-only Cloudflare / challenge-page detection result.

    RedirectHunter never attempts to bypass a challenge — this model exists
    purely to *flag* that a target is Cloudflare-protected so the operator
    can make an informed decision (e.g. exclude it from further automated
    testing).
    """

    model_config = ConfigDict(frozen=True)

    is_cloudflare: bool = False
    has_cf_ray: bool = False
    has_cf_cache_status: bool = False
    has_cf_clearance_cookie: bool = False
    has_cdn_cgi_path: bool = False
    is_challenge_page: bool = False
    cf_ray_id: str | None = None


class FingerprintInfo(BaseModel):
    """Server / CDN fingerprint derived from response headers."""

    model_config = ConfigDict(frozen=True)

    server_header: str | None = None
    powered_by_header: str | None = None
    detected_software: str | None = Field(
        default=None,
        description=(
            "Best-effort classification, e.g. 'nginx', 'Apache', 'LiteSpeed', "
            "'IIS', 'Cloudflare', 'CloudFront', 'Fastly', 'Varnish', 'Akamai'."
        ),
    )
    cloudflare: CloudflareStatus = Field(default_factory=CloudflareStatus)


class RedirectHop(BaseModel):
    """A single hop within a redirect chain.

    A chain of N redirects followed by a final 200 response produces N
    ``RedirectHop`` entries plus a terminal ``RedirectResult.final_url``.
    """

    model_config = ConfigDict(frozen=True)

    hop_index: int = Field(ge=0, description="0-based position in the chain.")
    url: str
    status_code: int | None = None
    redirect_type: RedirectType = RedirectType.NONE
    location_header: str | None = None
    server_header: str | None = None
    latency_ms: float = Field(ge=0.0)


class RedirectResult(BaseModel):
    """Complete outcome of validating a single candidate URL.

    This is the primary record persisted to the ``results`` table and
    emitted by every exporter. Field names intentionally mirror the
    "RESULT MODEL" section of the project specification.
    """

    model_config = ConfigDict(frozen=True)

    result_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scan_id: str
    source_url: str = Field(description="Raw URL as read from the input file, template intact.")
    expanded_url: str = Field(description="Source URL with {TARGET} substituted.")
    http_method: HTTPMethod
    status_code: int | None = None
    redirect_type: RedirectType = RedirectType.NONE
    location: str | None = Field(default=None, description="Raw Location header of the first hop.")
    final_url: str | None = None
    redirect_chain: list[RedirectHop] = Field(default_factory=list)
    hop_count: int = Field(default=0, ge=0)
    server: str | None = None
    content_type: str | None = None
    content_length: int | None = None
    cookies: dict[str, str] = Field(default_factory=dict)
    fingerprint: FingerprintInfo = Field(default_factory=FingerprintInfo)
    alive: bool = Field(description="True if the target responded without a transport-level error.")
    latency_ms: float = Field(ge=0.0, description="Total time for the full redirect chain.")
    error: str | None = Field(default=None, description="Transport/timeout error message, if any.")
    timestamp: datetime = Field(default_factory=_utcnow)

    @field_validator("redirect_chain")
    @classmethod
    def _chain_matches_hop_count(cls, v: list[RedirectHop]) -> list[RedirectHop]:
        return v


class ScanConfig(BaseModel):
    """Fully-resolved configuration for a single scan run.

    Instances are built by :mod:`redirecthunter.config` by layering, in
    increasing priority: built-in defaults -> YAML config file -> CLI flags.
    Nothing downstream (engine, detector, database) reads raw CLI args or
    raw YAML — everything consumes this validated model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_path: Path
    input_format: InputFormat
    input_table: str = Field(
        default="urls", description="Table name to read candidate URLs from (SQLite input only)."
    )
    input_column: str = Field(
        default="url", description="Column name holding candidate URLs (SQLite/CSV input only)."
    )
    target: str | None = Field(
        default=None, description="Replacement value substituted for the {TARGET} placeholder."
    )
    method: HTTPMethod = HTTPMethod.HEAD
    follow_redirects: bool = Field(
        default=True, description="If False, only the first hop is inspected (no chain-following)."
    )
    max_redirects: int = Field(default=10, ge=0, le=50)
    workers: int = Field(default=100, ge=1, le=2000)
    timeout: float = Field(default=10.0, gt=0.0)
    connect_timeout: float = Field(default=5.0, gt=0.0)
    retry: int = Field(default=2, ge=0, le=10)
    retry_backoff: float = Field(default=0.5, ge=0.0)
    rate_limit: float | None = Field(
        default=None, description="Maximum requests per second across all workers. None = unlimited."
    )
    http2: bool = True
    proxy: str | None = None
    user_agent: str = "RedirectHunter/1.0"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    verify_tls: bool = True
    database_path: Path = Field(default=Path("redirecthunter.db"))
    scan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scan_label: str | None = None

    @field_validator("target")
    @classmethod
    def _normalize_target(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        return v


class ScanSummary(BaseModel):
    """Aggregate statistics for a scan, used by the ``stats`` CLI command."""

    model_config = ConfigDict(frozen=True)

    scan_id: str
    label: str | None = None
    status: ScanStatus
    total_urls: int = 0
    completed: int = 0
    alive: int = 0
    dead: int = 0
    redirects_found: int = 0
    cloudflare_protected: int = 0
    avg_latency_ms: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def progress_pct(self) -> float:
        """Percentage of ``total_urls`` that have been completed."""
        if self.total_urls == 0:
            return 0.0
        return round((self.completed / self.total_urls) * 100, 2)


class DetectionOutcome(BaseModel):
    """Result of a single redirect-detector plugin run.

    Returned by every plugin in :mod:`redirecthunter.plugins`. ``None`` is
    returned by a plugin's ``detect()`` method (not this model) when no
    redirect is found — this model always represents a *positive* hit.
    """

    model_config = ConfigDict(frozen=True)

    redirect_type: RedirectType
    destination: str = Field(description="Raw, unresolved redirect target as found in source/header.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(description="Which plugin produced this outcome, e.g. 'http_location'.")
    raw_evidence: str | None = Field(
        default=None, description="Snippet of the header/HTML/script that triggered detection."
    )


class CandidateURL(BaseModel):
    """A single row parsed from an input file, prior to {TARGET} expansion."""

    model_config = ConfigDict(frozen=True)

    raw_url: str
    row_metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "HTTPMethod",
    "RedirectType",
    "InputFormat",
    "ExportFormat",
    "ScanStatus",
    "CloudflareStatus",
    "FingerprintInfo",
    "RedirectHop",
    "RedirectResult",
    "ScanConfig",
    "ScanSummary",
    "DetectionOutcome",
    "CandidateURL",
]
