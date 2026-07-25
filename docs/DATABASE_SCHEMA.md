# Database Schema

RedirectHunter persists every scan to a single SQLite file (default
`redirecthunter.db`, set via `--database`/`--db`) using four tables. This
document is generated directly from the live schema in `database.py` — see
that file's `_SCHEMA_SQL` constant as the single source of truth if this
ever needs re-verifying.

The database is opened in **WAL mode** (`PRAGMA journal_mode=WAL`) with
`PRAGMA foreign_keys=ON`, and every write goes through a single shared
connection guarded by an `asyncio.Lock` (see
[`ARCHITECTURE.md`](./ARCHITECTURE.md#persistence) for why).

## `scan`

One row per scan run (whether started fresh via `scan` or continued via
`resume` — a resumed scan reuses the same row, it does not create a new
one).

| Column | Type | Notes |
|---|---|---|
| `scan_id` | `TEXT` | Primary key. UUID4, auto-generated unless overridden. |
| `label` | `TEXT` | Optional human-readable label (`--label`). |
| `input_path` | `TEXT` | Path to the candidate-URL input file used. |
| `target` | `TEXT` | The `{TARGET}` replacement value (`--target`), if any. |
| `status` | `TEXT` | One of `running`, `completed`, `interrupted`, `failed`. |
| `total_urls` | `INTEGER` | Total candidate count, computed once at scan start. |
| `config_json` | `TEXT` | Full JSON-serialized `ScanConfig` snapshot — this is what makes `resume` work without re-specifying every flag. |
| `started_at` | `TEXT` | ISO-8601 UTC timestamp. |
| `finished_at` | `TEXT` | ISO-8601 UTC timestamp, `NULL` until the scan reaches a terminal status. |

## `results`

One row per candidate URL processed. See
[`ARCHITECTURE.md`](./ARCHITECTURE.md#redirect-chain-semantics) for what
`status_code`/`redirect_type`/`location` vs. `final_url`/`server`/etc.
each actually describe.

| Column | Type | Notes |
|---|---|---|
| `result_id` | `TEXT` | Primary key. UUID4. |
| `scan_id` | `TEXT` | FK → `scan.scan_id`, `ON DELETE CASCADE`. |
| `source_url` | `TEXT` | Raw candidate URL, `{TARGET}` template intact. |
| `expanded_url` | `TEXT` | `source_url` with `{TARGET}` substituted. |
| `http_method` | `TEXT` | `HEAD` or `GET`. |
| `status_code` | `INTEGER` | Nullable — `NULL` if the request never got a response. |
| `redirect_type` | `TEXT` | One of the `RedirectType` enum values (see below). |
| `location` | `TEXT` | Raw `Location` header of the *first* hop. |
| `final_url` | `TEXT` | URL of the terminal response actually landed on. |
| `hop_count` | `INTEGER` | Number of redirects followed (0 = no redirect). |
| `server` | `TEXT` | `Server` header of the terminal response. |
| `content_type` | `TEXT` | `Content-Type` header of the terminal response. |
| `content_length` | `INTEGER` | Parsed `Content-Length`, if numeric and present. |
| `cookies_json` | `TEXT` | JSON object: `{name: value}` from the terminal response's `Set-Cookie` headers. |
| `fingerprint_json` | `TEXT` | JSON-serialized `FingerprintInfo` (server/CDN software + full Cloudflare classification). |
| `alive` | `INTEGER` | `0`/`1` — whether any response was received without a transport-level error. |
| `latency_ms` | `REAL` | Total wall-clock time for the full chain. |
| `error` | `TEXT` | Transport/timeout error message, if `alive = 0`. |
| `timestamp` | `TEXT` | ISO-8601 UTC completion timestamp. |

Indexes: `idx_results_scan_id (scan_id)`, `idx_results_scan_source
(scan_id, source_url)` — the latter backs `resume`'s "which URLs are
already done" lookup.

**`redirect_type` values:** `301_moved_permanently`, `302_found`,
`303_see_other`, `307_temporary_redirect`, `308_permanent_redirect`,
`meta_refresh`, `javascript`, `none`.

## `chain`

One row per redirect hop actually followed (not including the terminal
response — see `hop_count` above).

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Autoincrement primary key. |
| `result_id` | `TEXT` | FK → `results.result_id`, `ON DELETE CASCADE`. |
| `hop_index` | `INTEGER` | 0-based position in the chain. |
| `url` | `TEXT` | The URL requested at this hop. |
| `status_code` | `INTEGER` | This hop's HTTP status. |
| `redirect_type` | `TEXT` | This hop's redirect classification. |
| `location_header` | `TEXT` | This hop's raw `Location` header (if HTTP redirect). |
| `server_header` | `TEXT` | This hop's `Server` header. |
| `latency_ms` | `REAL` | Time taken for this specific hop's request. |

Index: `idx_chain_result_id (result_id)`.

## `headers`

The **full** header set of the terminal response only — not every
intermediate hop. Intermediate hops' audit-relevant fields (status,
Location, Server) are already on `chain`; duplicating every header of
every 30x response would multiply storage several-fold for negligible
audit value. The terminal response — the page actually landed on — is
where a complete header set (cookies, CSP, cache headers, etc.) is worth
keeping.

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` | Autoincrement primary key. |
| `result_id` | `TEXT` | FK → `results.result_id`, `ON DELETE CASCADE`. |
| `header_name` | `TEXT` | Header name, as received. |
| `header_value` | `TEXT` | Header value. |

Index: `idx_headers_result_id (result_id)`.

## Example queries

Every one of these works against any `redirecthunter.db` produced by a
real scan — they're not hypothetical.

```sql
-- All Cloudflare-protected targets in a scan
SELECT source_url, final_url
FROM results
WHERE scan_id = '...'
  AND json_extract(fingerprint_json, '$.cloudflare.is_cloudflare') = 1;

-- Every full redirect chain, longest first
SELECT r.source_url, r.hop_count, c.hop_index, c.url, c.status_code
FROM results r
JOIN chain c ON c.result_id = r.result_id
WHERE r.scan_id = '...'
ORDER BY r.hop_count DESC, c.hop_index ASC;

-- Distinct final destinations an open-redirect endpoint pointed to
SELECT DISTINCT final_url
FROM results
WHERE scan_id = '...' AND redirect_type != 'none';

-- All response headers seen on a specific result
SELECT header_name, header_value
FROM headers
WHERE result_id = (SELECT result_id FROM results WHERE source_url = '...' AND scan_id = '...');
```

## Exporting to a standalone SQLite file

`redirecthunter export <scan_id> out.db --format sqlite` produces a
**complete, independent** copy of all four tables (filtered to one scan)
via `ATTACH DATABASE` + `INSERT ... SELECT` — not a partial dump. The
resulting file can be opened with the standard `sqlite3` CLI or any
SQLite client with no dependency on RedirectHunter at all.
