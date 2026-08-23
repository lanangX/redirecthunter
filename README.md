# RedirectHunter

**An HTTP Redirect Discovery and Validation Framework** for security
auditing, QA, SEO research, redirect inventory, and migration validation.

RedirectHunter is **not** an exploitation tool. It validates redirect
behavior on candidate URLs you already have — checking where they go,
what type of redirect they use, and what's serving the destination. It
never attempts to bypass any protection it encounters (Cloudflare
challenges included) — it only classifies and reports.

Use it to:

- Audit a list of known/legacy redirect endpoints during a **security
  review** of your own infrastructure
- Verify open-redirect parameters point where they're supposed to, as
  part of **authorized penetration testing**
- Validate a **site migration** actually 301s every old URL to the
  correct new one
- Build a **redirect inventory** across a large domain
- Check **SEO** redirect chains for excessive hop counts or unexpected
  final destinations

## Table of contents

- [Screenshots](#screenshots)
- [Features](#features)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [CLI reference](#cli-reference)
- [Input formats](#input-formats)
- [Configuration file](#configuration-file)
- [Understanding the results](#understanding-the-results)
- [Guides](#guides) — crawling, backlink verification (`bl-check`/`bl-chain`), FAQ
- [Architecture at a glance](#architecture-at-a-glance)
- [Performance](#performance)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Related tools in this repo](#related-tools-in-this-repo)
- [License & Contributing](#license)

---

## Screenshots

Real output from an actual scan (captured via Rich's SVG export — not
mockups; see [`docs/generate_screenshots.py`](docs/generate_screenshots.py)):

**Scan progress and summary**

![Scan summary](docs/images/scan_summary.svg)

**Individual results**

![Show results](docs/images/show_results.svg)

**Scan history**

![Stats listing](docs/images/stats_list.svg)

---

## Features

- **Four redirect-detection strategies**, run in priority order: HTTP
  status codes (301/302/303/307/308) + `Location` header, `<meta
  http-equiv="refresh">`, and static inline JavaScript pattern matching
  (`window.location`, `location.href`, `.assign()`, `.replace()`).
- **Cloudflare-aware, never Cloudflare-bypassing.** Detects `cf-ray`,
  `cf-cache-status`, `cf_clearance` cookies, `/cdn-cgi/` paths, and
  challenge-page markers — and only ever *flags* protected targets.
- **Server/CDN fingerprinting**: nginx, Apache, LiteSpeed, IIS, Cloudflare,
  CloudFront, Fastly, Varnish, Akamai.
- **Four input formats**: TXT, CSV, JSON, SQLite — with a `{TARGET}`
  templating system for testing the same set of redirect parameters
  against different destinations.
- **Async engine built for scale**: a shared, connection-pooled HTTP/2
  client, a bounded worker pool (tested up to 500 concurrent workers),
  retry-with-exponential-backoff, and an aggregate rate limiter — all
  designed to stay memory-bounded scanning 100k+ URLs.
- **Resumable scans**: every scan's exact configuration is snapshotted to
  SQLite; `redirecthunter resume` picks up exactly where an interrupted
  run left off.
- **Short scan IDs everywhere**: every command accepts an unambiguous
  prefix of a scan_id (like a git short hash), not just the full UUID.
- **`redirecthunter find`**: filter results to redirects landing outside
  a given domain — the core open-redirect audit question — and optionally
  save the result as a plain link list, one URL per line.
- **`redirecthunter delete` / `redirecthunter vacuum`**: permanently
  remove a scan (cascades to its results/chain/headers automatically)
  and reclaim the disk space it occupied.
- **Streaming exports** to CSV, JSON, or a standalone SQLite file — none
  of them buffer a full result set in memory.
- **Live Rich progress**: worker count, throughput, success/failure
  counts, and ETA, updated in real time.
- **`redirecthunter redact-target` / `redirecthunter expand-target`**:
  domain ↔ `{TARGET}` token conversion as standalone commands — no scan
  required.
- **`redirecthunter crawl`**: Ahrefs-Site-Audit-style site crawler that
  discovers pages, flags on-page SEO issues, and checks every link for
  broken status. See the [crawl guide](docs/CRAWL_GUIDE.md).
- **`redirecthunter bl-check`**: SQLite-persisted backlink verification —
  checks every URL in a file for a genuine outbound link to a target
  domain. See the [backlink guide](docs/BACKLINK_GUIDE.md).
- **`redirecthunter bl-chain`**: runs `bl-check` across a *tiered/pyramid*
  link structure (tier 1 vs. your domain, tier 2 vs. tier 1's hosts, and
  so on) in a single command. See
  [Chained/tiered audits](docs/BACKLINK_GUIDE.md#chained-tiered-audits-redirecthunter-bl-chain).

## Architecture at a glance

```
input file (.txt/.csv/.json/.db)
        │
        ▼
   loader.py  ──▶  engine.py  ──▶  analyzer.py  ──▶  detector.py + plugins/
   (candidates)    (HTTP, retry,    (response ->        (redirect-type
                    rate limit,      RedirectResult)      detection pipeline
                    worker pool)                          + Cloudflare
        │                │                                classification)
        │                ▼
        │          database.py (SQLite: scan / results / chain / headers,
        │                        crawls / crawl_pages / crawl_links)
        │                ▲
        │                │
        │          crawler.py (BFS frontier, discovers its own work list --
        │                       see the crawl guide)
        │                │
        └──────────▶ cli.py (Typer + Rich)  ──▶  export/ (CSV/JSON/SQLite)
```

Full design rationale — including the redirect-chain semantics
(`hop_count`, what `status_code` vs. `final_url` actually describe, why
the worker pool is structured the way it is, and how crawl mode's dynamic
frontier differs from scan's fixed candidate list) — is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Related tools in this repo

This is a monorepo: the CLI/library above is the core, and these two live
alongside it as independent, no-build sibling projects (neither is a
dependency of the other, and neither is packaged into the `redirecthunter`
Python distribution — see `[tool.setuptools.packages.find]` in
`pyproject.toml`).

- **[`redirecthunter-dashboard/`](redirecthunter-dashboard/)** — a
  single-file, offline HTML dashboard for browsing a `redirecthunter.db`
  visually (scan overview, distribution charts, filterable results table,
  detail drawer) without touching the CLI. Open
  `redirecthunter-dashboard/index.html` directly in a browser and drag a
  `.db` file onto it; everything runs client-side, your data never leaves
  the browser. See `redirecthunter-dashboard/README.md`.

- **[`rh-cookie-copier/`](rh-cookie-copier/)** — a small cross-browser
  extension (Chrome/Edge/Brave/Opera/Firefox) that copies a site's session
  cookies as a ready-to-paste `bl-check --accounts-file` line, and can also
  apply a pasted cookie line to the current browser tab so a login-walled
  backlink can be checked visually. See `rh-cookie-copier/README.md`.

---

## Installation

Requires **Python 3.12+**.

```bash
git clone https://github.com/lanangX/redirecthunter.git
cd redirecthunter
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

Or install the runtime dependencies directly:

```bash
pip install -r requirements.txt
```

Verify the install:

```bash
redirecthunter --help
```

**Short alias:** every command is also available as `rh` (e.g. `rh scan
urls.txt`, `rh crawl ...`) — a shorter alias for the same binary, added
for faster day-to-day typing. `redirecthunter` and `rh` are interchangeable
everywhere; this README keeps using the full name for clarity.

### Optional: faster event loop

On Linux/macOS, install the `speed` extra for `uvloop`:

```bash
pip install -e ".[speed]"
```

### Optional: browser rendering for `bl-check --browser` / `bl-chain --browser`

Needed only if you'll check JS-rendered single-page apps (YouTube,
Instagram, X/Twitter, and similar) — see the
[backlink guide](docs/BACKLINK_GUIDE.md#static-html-vs-a-real-rendered-browser---browser):

```bash
pip install "redirecthunter[js]"
playwright install chromium
```

### Development install

```bash
pip install -e ".[dev]"
pytest                              # 369 tests
ruff check redirecthunter/ tests/   # lint
```

---

## Quickstart

```bash
# 1. Point it at a candidate URL list and a target to test against
redirecthunter scan examples/urls.txt --target https://example.org --method GET

# 2. Check what it found
redirecthunter stats <scan_id>
redirecthunter show <scan_id> --redirects-only

# 3. Export for further analysis
redirecthunter export <scan_id> results.csv

# 4. Done with this scan? Remove it and reclaim the disk space
redirecthunter delete <scan_id> --vacuum
```

`{TARGET}` in any candidate URL (`https://a.com/go?url={TARGET}`) is
replaced with the value passed via `--target` before the request is made.
URLs without the placeholder are requested exactly as written.

---

## CLI reference

19 commands across four families, all sharing the same `redirecthunter.db`:

- **`scan`** — `scan`, `resume`, `stats`, `export`, `show`, `find`,
  `delete`, `vacuum` (this section covers the everyday ones)
- **`crawl`** — `crawl`, `crawl-stats`, `crawl-export`, `crawl-show` (see
  the [crawl guide](docs/CRAWL_GUIDE.md))
- **`bl-check` / `bl-chain`** — `bl-check`, `bl-chain`, `bl-stats`,
  `bl-show`, `bl-export` (see the [backlink guide](docs/BACKLINK_GUIDE.md))
- **Standalone, no scan required** — `redact-target`, `expand-target`

Full flag-by-flag reference (generated from real `--help` output):
[`docs/CLI_REFERENCE.md`](docs/CLI_REFERENCE.md).

```bash
# Basic scan
redirecthunter scan urls.txt

# With a target and higher concurrency
redirecthunter scan urls.txt --target https://example.org --workers 500

# GET is required to catch meta-refresh / JS redirects (HEAD has no body)
redirecthunter scan urls.txt --method GET

# Through a SOCKS5 proxy
redirecthunter scan urls.txt --proxy socks5://127.0.0.1:1080

# Rate-limited, respectful scanning of third-party infrastructure
redirecthunter scan urls.txt --rate-limit 10

# Resume an interrupted scan
redirecthunter resume <scan_id>

# Find redirects landing outside the target domain, save as a plain link list
redirecthunter find <scan_id> --output external_redirects.txt

# Or the opposite: confirm which source URLs actually redirect to your target
redirecthunter find <scan_id> --invert --field source --output confirmed_backlinks.txt

# Every command accepts a short scan_id prefix, not just the full UUID
redirecthunter show 3f9a1c2e --redirects-only

# Everything recorded across all scans in a database
redirecthunter stats

# Only Cloudflare-protected results
redirecthunter show <scan_id> --cloudflare-only

# Export formats
redirecthunter export <scan_id> results.csv
redirecthunter export <scan_id> results.json --format json
redirecthunter export <scan_id> results.db  --format sqlite

# Permanently delete a scan you no longer need
redirecthunter delete <scan_id>
redirecthunter delete <scan_id> --yes --vacuum   # skip confirmation, reclaim disk space immediately

# Reclaim disk space after deleting scans (SQLite doesn't shrink the file automatically)
redirecthunter vacuum

# Standalone domain <-> {TARGET} conversion, no scan required
redirecthunter redact-target urls.txt -d medilana.id -o out.txt
redirecthunter expand-target out.txt --target https://medilana.id

# export cuma redirect saja
redirecthunter export <scan_id> redirects.csv --redirects-only

# export cuma yang body-nya punya link <a href> (scan HARUS pakai --method GET,
# lihat FAQ "Kenapa --has-link-only selalu 0 hasil?" di docs/FAQ.md)
redirecthunter export <scan_id> links.csv --has-link-only

# export cuma status code tertentu, atau satu kelas status sekaligus
redirecthunter export <scan_id> redirects_301.csv --status-code 301
redirecthunter export <scan_id> redirects_broken.csv --status-code 4xx,5xx

# bisa digabung (semua filter di-AND-kan)
redirecthunter export <scan_id> redirect_dengan_link.csv --redirects-only --has-link-only

# preview dulu di terminal sebelum export
redirecthunter show <scan_id> --has-link-only --limit 100
redirecthunter show <scan_id> --status-code 301,302,404
```

---

## Input formats

| Format | Example | Notes |
|---|---|---|
| TXT | [`examples/urls.txt`](examples/urls.txt) | One URL per line, `#` comments supported. |
| CSV | [`examples/urls.csv`](examples/urls.csv) | Header row names the URL column (`--input-column`); other columns kept as metadata. |
| JSON | [`examples/urls.json`](examples/urls.json) | Array of strings and/or `{"url": "...", ...}` objects. |
| SQLite | [`examples/urls.db`](examples/urls.db) | `--input-table` / `--input-column` (defaults: `urls` / `url`). |

See [`examples/README.md`](examples/README.md) for a full walkthrough of
each format.

## Configuration file

Any flag can also be set in a YAML file, layered as: **CLI flags > YAML
file > built-in defaults**. See the fully-annotated
[`examples/redirecthunter.yaml`](examples/redirecthunter.yaml):

```yaml
target: https://example.org
method: GET
workers: 300
timeout: 8
rate_limit: null
extra_headers:
  X-Scan-Purpose: "authorized-security-audit"
```

```bash
redirecthunter scan urls.txt --config redirecthunter.yaml
# or drop it in your working directory as `redirecthunter.yaml` for auto-discovery
```

---

## Understanding the results

The single most important thing to know: **`redirect_chain` contains only
the redirects actually followed, not the terminal response.** So for a
URL that 301s once and lands on a 200:

- `status_code` / `redirect_type` / `location` describe the **first**
  response (does this URL redirect, and to what?)
- `final_url` / `server` / `fingerprint` describe the **terminal**
  response (where did it actually end up, and what's serving it?)
- `hop_count` is `1` — the number of redirects, not the number of HTTP
  requests made
- `body_link` (the first navigable `<a href>` in the terminal response's
  body) is only ever populated on scans run with `--method GET` — HEAD
  requests have no body to inspect it in, regardless of what's on the
  page. See [`docs/FAQ.md`](docs/FAQ.md) if you hit this.

Full field-by-field reference: [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md).

---

## Guides

Longer walkthroughs that used to live inline in this README now have
their own pages, so this file stays a quick-reference rather than a wall
of text:

- **[`docs/CRAWL_GUIDE.md`](docs/CRAWL_GUIDE.md)** — everything about
  `redirecthunter crawl`: domain mode vs. URL-list mode, on-page SEO
  checks, broken-link handling, and the `crawl-*` companion commands.
- **[`docs/BACKLINK_GUIDE.md`](docs/BACKLINK_GUIDE.md)** — everything
  about `bl-check` and `bl-chain`: static vs. `--browser` mode, per-row
  target overrides, tiered/pyramid audits, the exported CSV/JSON columns,
  `match_type` values, and platform-specific quirks (LinkedIn, bit.ly,
  YouTube/Instagram, etc.) worth knowing before you interpret a report.
- **[`docs/FAQ.md`](docs/FAQ.md)** — the most common gotchas (why
  `--has-link-only` comes back empty, `hop_count` vs. request count, short
  scan-ID prefixes, resuming interrupted scans, `bl-check` vs. `bl-chain`,
  and more).

---

## Performance

The engine is built around a single shared, connection-pooled
`httpx.AsyncClient` and a bounded `asyncio.Queue` producer/consumer pool —
not one client per worker and not a list of 100k pre-materialized tasks.
Verified in testing:

- Rate limiter correctly paces aggregate throughput regardless of worker
  count (`--rate-limit 3` measurably takes ~1.75s for 6 requests vs.
  ~0.04s unlimited).
- Retry-with-backoff recovers from transient connection failures on the
  expected schedule.
- GET response bodies are capped at 2MB and streamed, since redirect
  detection only ever needs the `<head>` of a page.

See [`docs/ARCHITECTURE.md#concurrency-model`](docs/ARCHITECTURE.md#concurrency-model)
for the full design rationale.

---

## Testing

369 tests, `respx`-mocked HTTP (no live network required), plus real
SQLite round-trips and full CLI workflow tests via Typer's `CliRunner`.

```bash
pytest                                          # full suite
pytest --cov=redirecthunter --cov-report=term-missing   # with coverage
ruff check redirecthunter/ tests/               # lint
```

---

## Project structure

```
redirecthunter/
├── cli.py            Typer CLI + Rich progress/tables
├── config.py          Layered YAML/CLI configuration
├── engine.py           Async worker pool, HTTP client, retries, rate limiting
├── analyzer.py           Response -> RedirectResult
├── detector.py             Redirect-detection pipeline orchestrator
├── plugins/                  http_location, meta_refresh, javascript, cloudflare
├── fingerprint.py               Server/CDN fingerprinting
├── database.py                    aiosqlite persistence
├── export/                          Streaming CSV/JSON/SQLite export
│   ├── filters.py                     ExportFilter/ExportError (shared with `show`)
│   ├── csv_writer.py                  Streaming CSV writer
│   ├── json_writer.py                 Streaming JSON writer
│   └── service.py                     Exporter — format dispatch
├── crawler.py                          Async BFS site crawler (`crawl`)
├── backlink.py                          bl-check / bl-chain implementation
├── loader.py                          TXT/CSV/JSON/SQLite input loading
├── logger.py                             Rich logging setup
├── models.py                                Pydantic data contracts
└── utils.py                                    URL/cookie/formatting helpers

tests/        334 pytest tests (respx-mocked HTTP, real SQLite, real CLI,
              real headless Chromium for bl-check/bl-chain --browser)
examples/     Sample input files (all 4 formats) + sample YAML config
docs/         Architecture, CLI reference, database schema, guides, screenshots
```

There is no separate `scripts/` directory anymore: `backlink_checker.py`/
`backlink_checker_js.py` were folded into `bl-check`/`bl-chain` (see
`MEMORY.md` for the full reasoning behind that decision).

## License

MIT — see [`LICENSE`](LICENSE).

## Contributing

Issues and pull requests welcome at
[github.com/lanangX/redirecthunter](https://github.com/lanangX/redirecthunter).
Please run `pytest` and `ruff check` before submitting.

Packaging a local checkout into a zip (for sharing or backup)? See
[`docs/PACKAGING.md`](docs/PACKAGING.md).
