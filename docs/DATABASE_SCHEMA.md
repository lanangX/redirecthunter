# Database Schema

RedirectHunter persists every scan to a single SQLite file (default
`redirecthunter.db`, set via `--database`/`--db`) using four tables for
`scan`/`resume`, plus three more for `crawl`. This document is generated
directly from the live schema in `database.py` — see that file's
`_SCHEMA_SQL` constant as the single source of truth if this ever needs
re-verifying.

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
| `body_link` | `TEXT` | Nullable. Raw `href` of the first navigable `<a>` tag found in the terminal response's body (e.g. a manual "click here to continue" interstitial). Only ever populated when the scan was run with `--method GET` — `HEAD` requests have no body to extract it from, so this is `NULL` for every row of a HEAD scan regardless of what the live page actually contains. |
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
**complete, independent** copy of all four `scan`/`resume` tables (filtered
to one scan) via `ATTACH DATABASE` + `INSERT ... SELECT` — not a partial
dump. The resulting file can be opened with the standard `sqlite3` CLI or
any SQLite client with no dependency on RedirectHunter at all.
`crawl-export` does not support `--format sqlite` (CSV/JSON only) — see
below for why.

---

## Crawl-mode tables

`crawl` (and `crawl-stats`/`crawl-export`/`crawl-show`) use three separate
tables rather than reusing `scan`/`results`/`chain` above — a crawl's unit
of work is a *page* with on-page SEO fields a redirect-validation result
has no use for, plus a many-per-page *link* record, a genuinely different
shape from one row per candidate URL. See
[`ARCHITECTURE.md`](./ARCHITECTURE.md#crawl-mode) for the fuller design
rationale (why a dynamic frontier needs its own worker-pool termination
strategy, and why a dead internal page vs. a checked link are recorded
differently).

### `crawls`

One row per crawl run.

| Column | Type | Notes |
|---|---|---|
| `crawl_id` | `TEXT` | Primary key. UUID4, auto-generated unless overridden. |
| `label` | `TEXT` | Optional human-readable label (`--label`). |
| `seed_mode` | `TEXT` | `domain` (discover from one seed URL) or `url_list` (seed from an input file). |
| `seed_url` | `TEXT` | The seed URL, for `domain` mode. |
| `seed_input_path` | `TEXT` | The seed input file path, for `url_list` mode. |
| `status` | `TEXT` | One of `running`, `completed`, `interrupted`, `failed`. |
| `config_json` | `TEXT` | Full JSON-serialized `CrawlConfig` snapshot. |
| `started_at` | `TEXT` | ISO-8601 UTC timestamp. |
| `finished_at` | `TEXT` | ISO-8601 UTC timestamp, `NULL` until the crawl reaches a terminal status. |

### `crawl_pages`

One row per page actually fetched (the seed(s), plus every in-scope
internal link discovered from them — including a *dead* internal link,
which still gets a row here with `status_code >= 400`, not a
`crawl_links` row; see below).

| Column | Type | Notes |
|---|---|---|
| `page_id` | `TEXT` | Primary key. UUID4. |
| `crawl_id` | `TEXT` | FK → `crawls.crawl_id`, `ON DELETE CASCADE`. |
| `url` | `TEXT` | The page's URL. |
| `depth` | `INTEGER` | 0 for a seed, N for a page discovered N hops from one. |
| `discovered_from` | `TEXT` | The page whose link led here. `NULL` for seeds. |
| `status_code` | `INTEGER` | Nullable — `NULL` if the request never got a response. |
| `alive` | `INTEGER` | `0`/`1` — whether *any* response was received (a 404 is still `alive = 1`; see `status_code` for whether it was a healthy one). |
| `redirected` | `INTEGER` | `0`/`1` — whether the request followed one or more redirects to get here. |
| `final_url` | `TEXT` | Post-redirect URL, if `redirected = 1`. |
| `content_type` | `TEXT` | `Content-Type` header of the response. |
| `title` | `TEXT` | `<title>` text, if present. |
| `title_length` | `INTEGER` | Character length of `title`. |
| `meta_description` | `TEXT` | `<meta name="description">` content, if present. |
| `meta_description_length` | `INTEGER` | Character length of `meta_description`. |
| `h1_json` | `TEXT` | JSON array of every `<h1>` tag's text. |
| `h1_count` | `INTEGER` | `len(h1_json)`, denormalized for fast filtering/aggregation. |
| `internal_link_count` | `INTEGER` | Count of unique internal link targets found on this page. |
| `external_link_count` | `INTEGER` | Count of unique external link targets found on this page. |
| `word_count` | `INTEGER` | Rough word count of the `<body>` text. |
| `issues_json` | `TEXT` | JSON array of `PageIssue` values this page's own response proved (title/meta/H1 problems). Does **not** include "links to something broken" — see `crawl_links` and `iter_crawl_pages(broken_links_only=True)`. |
| `latency_ms` | `REAL` | Wall-clock time for this page's request. |
| `error` | `TEXT` | Transport/timeout error message, if `alive = 0`. |
| `timestamp` | `TEXT` | ISO-8601 UTC completion timestamp. |

Indexes: `idx_crawl_pages_crawl_id (crawl_id)`, `idx_crawl_pages_crawl_url
(crawl_id, url)`.

### `crawl_links`

One row per **checked link occurrence** that was *not* itself promoted to
a `crawl_pages` row — external links, internal links past
`--max-depth`/`--max-pages`, and every occurrence of an internal link
*after* its first (the first occurrence is what got promoted to a page; a
page is only ever fetched once per crawl no matter how many pages link to
it). A dead in-scope internal link shows up as a `crawl_pages` row with
`status_code >= 400` instead of a row here.

| Column | Type | Notes |
|---|---|---|
| `link_id` | `TEXT` | Primary key. UUID4. |
| `crawl_id` | `TEXT` | FK → `crawls.crawl_id`, `ON DELETE CASCADE`. |
| `source_page_url` | `TEXT` | The page this link was found on. |
| `target_url` | `TEXT` | Resolved, absolute URL the link points to. |
| `raw_href` | `TEXT` | Original, unresolved `href` attribute text. |
| `link_kind` | `TEXT` | `internal` or `external`. |
| `anchor_text` | `TEXT` | Visible `<a>` text, if any. |
| `rel` | `TEXT` | Raw, unparsed `rel` attribute of the anchor, if any (e.g. `"nofollow"`, `"sponsored ugc"`). |
| `target_attr` | `TEXT` | Raw `target` attribute of the anchor, if any (e.g. `"_blank"`). |
| `status_code` | `INTEGER` | Nullable — `NULL` on a transport-level failure. |
| `is_broken` | `INTEGER` | `0`/`1` — `1` if `status_code >= 400` or the request failed outright. |
| `redirected` | `INTEGER` | `0`/`1`. |
| `final_url` | `TEXT` | Post-redirect URL, if `redirected = 1`. |
| `error` | `TEXT` | Transport/timeout error message, if the check failed outright. |
| `latency_ms` | `REAL` | Wall-clock time for this check (`0` on a cache hit — see below). |
| `checked_at` | `TEXT` | ISO-8601 UTC timestamp. |

The underlying HTTP check is deduplicated **per crawl**: the same
`target_url` linked from ten different pages is only ever requested once
over the network (`Crawler`'s in-memory link-status cache) — but every
occurrence still gets its own row here, so "which pages link to this
broken URL" stays answerable with a plain `WHERE target_url = ...` query.

Indexes: `idx_crawl_links_crawl_id (crawl_id)`, `idx_crawl_links_broken
(crawl_id, is_broken)`, `idx_crawl_links_source (crawl_id,
source_page_url)`.

### Crawl example queries

```sql
-- Every broken link (or dead promoted page) discovered in a crawl
SELECT source_page_url, target_url, status_code FROM crawl_links
WHERE crawl_id = '...' AND is_broken = 1
UNION ALL
SELECT discovered_from, url, status_code FROM crawl_pages
WHERE crawl_id = '...' AND discovered_from IS NOT NULL AND (alive = 0 OR status_code >= 400);

-- Pages missing a title or meta description
SELECT url, title, meta_description FROM crawl_pages
WHERE crawl_id = '...' AND (title IS NULL OR meta_description IS NULL);

-- Duplicate titles across the crawl
SELECT title, COUNT(*) AS n, GROUP_CONCAT(url, ' | ') AS urls
FROM crawl_pages
WHERE crawl_id = '...' AND title IS NOT NULL AND title != ''
GROUP BY title HAVING COUNT(*) > 1;
```

`crawl-export` streams straight from `iter_crawl_pages`/`iter_crawl_links`
to CSV or JSON — there's no `--format sqlite` for crawl exports, since
(unlike `export`'s `ATTACH DATABASE` copy of the whole `scan`/`results`/
`chain` row-set) a crawl's two related tables would need the same
attach-and-copy treatment for comparatively little benefit over "open
`redirecthunter.db` directly and run the queries above"; it was left out
of v1 to keep scope tight rather than because it's structurally harder.

## Backlink-check tables

`bl-check` (and `bl-stats`/`bl-show`/`bl-export`) use two tables, the same
"one row per run" + "one row per thing checked" shape `crawls`/
`crawl_pages` use above — but a checked backlink URL has an entirely
different set of columns from a crawl page (match type, matched href,
rather than on-page SEO fields), so it isn't folded into the crawl tables.
The matching logic itself (what counts as a genuine outbound link, vs. a
text-only mention, vs. a weaker indirect/tracker-embedded match) lives in
`redirecthunter/backlink.py`, shared between the default (httpx) check
and the `--browser` (Playwright) one — see `MEMORY.md` for why this is
one CLI command with a mode flag rather than two separate tools, which
it used to be.

### `backlink_checks`

One row per `bl-check` run.

| Column | Type | Notes |
|---|---|---|
| `backlink_id` | `TEXT` | Primary key. UUID4, auto-generated unless overridden. |
| `label` | `TEXT` | Optional human-readable label (`--label`/`-l`). |
| `domain` | `TEXT` | The target domain this run checked for, e.g. `medilana.id`. |
| `input_path` | `TEXT` | The input file path this run was given. |
| `status` | `TEXT` | One of `running`, `completed`, `interrupted`, `failed`. |
| `config_json` | `TEXT` | Full JSON-serialized `BacklinkCheckConfig` snapshot. |
| `started_at` | `TEXT` | ISO-8601 UTC timestamp. |
| `finished_at` | `TEXT` | ISO-8601 UTC timestamp, `NULL` until the run reaches a terminal status. |

### `backlink_results`

One row per URL checked.

| Column | Type | Notes |
|---|---|---|
| `result_id` | `TEXT` | Primary key. UUID4. |
| `backlink_id` | `TEXT` | FK → `backlink_checks.backlink_id`, `ON DELETE CASCADE`. |
| `source_url` | `TEXT` | The URL that was checked. |
| `final_url` | `TEXT` | Post-redirect URL actually fetched, if different from `source_url`. |
| `status_code` | `INTEGER` | Nullable — `NULL` on a transport-level failure. |
| `match_found` | `INTEGER` | `0`/`1` — whether a genuine (or indirect) outbound link to the target domain was found. |
| `match_type` | `TEXT` | `anchor`, `subdomain_anchor`, `final_url_is_target`, `indirect_query`, `text_mention_only`, or `not_found`. Only the first four count as `match_found = 1`. |
| `matched_href` | `TEXT` | The raw `href` value the match was found in, if any. |
| `rel` | `TEXT` | Raw, unparsed `rel` attribute of the matched anchor, if any (e.g. `"nofollow"`, `"sponsored ugc"`). |
| `target` | `TEXT` | Raw `target` attribute of the matched anchor, if any (e.g. `"_blank"`). |
| `matched_target` | `TEXT` | Which member of the (possibly multi-domain) target `frozenset` this result actually matched, e.g. `"form.medilana.com"` out of a `medilana.id;form.medilana.com` override. `NULL` when `match_found = 0`. Added after the initial schema -- see the migration note below. |
| `blocked` | `INTEGER` | `0`/`1` — `1` if the response looked like an anti-scraping block (e.g. LinkedIn's HTTP 999) or a Cloudflare/bot-challenge interstitial, not a real "not found." |
| `requires_login` | `INTEGER` | `0`/`1` — `1` if the request was redirected to a login wall instead of serving the real page. |
| `text_mentions` | `INTEGER` | Count of plain-text (not inside any `<a href>`) mentions of the domain found on the page. |
| `robots_meta` | `TEXT` | Raw `content` of the page's `<meta name="robots">` tag, if present (e.g. `"noindex, follow"`). Added after the initial schema -- see the migration note below. |
| `robots_header` | `TEXT` | Raw value of the `X-Robots-Tag` response header, if present. Deliberately a separate column from `robots_meta`, not merged -- the two can disagree (the header takes precedence per Google's own spec), and merging them would silently hide that disagreement. Added after the initial schema -- see the migration note below. |
| `notes` | `TEXT` | Human-readable caveats about this result, `" | "`-delimited (e.g. why a weaker `indirect_query` match should be manually verified). |
| `error` | `TEXT` | Transport/timeout error message, if the check failed outright. |
| `checked_at` | `TEXT` | ISO-8601 UTC timestamp. |

Indexes: `idx_backlink_results_backlink_id (backlink_id)`,
`idx_backlink_results_match (backlink_id, match_found)`.

`robots_meta`/`robots_header`/`matched_target` were added to
`backlink_results` after `bl-check` first shipped, via the same additive
`PRAGMA table_info` + `ALTER TABLE ADD COLUMN` migration `body_link`/
`rel`/`target_attr` used on `results`/`crawl_links` -- existing
`redirecthunter.db` files pick up the new columns (as `NULL` for
previously-recorded rows) the next time any command opens them; no
manual migration step is needed.

### Backlink-check example queries

```sql
-- Every confirmed backlink from a run
SELECT source_url, matched_href, match_type FROM backlink_results
WHERE backlink_id = '...' AND match_found = 1;

-- URLs that only mention the domain as plain text (never linked)
SELECT source_url, text_mentions FROM backlink_results
WHERE backlink_id = '...' AND match_type = 'text_mention_only';

-- Pages that couldn't be evaluated at all (blocked or login-walled)
SELECT source_url, blocked, requires_login FROM backlink_results
WHERE backlink_id = '...' AND (blocked = 1 OR requires_login = 1);
```

`bl-export` streams straight from `iter_backlink_results` to CSV or
JSON, the same way `crawl-export` does — no `--format sqlite`, for the
same v1-scope reason noted above.
