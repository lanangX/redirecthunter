# Changelog

All notable changes to RedirectHunter are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [SemVer](https://semver.org/).

## [Unreleased]

### Removed

- `bl-check`/`bl-chain`'s `-H`/`--header` and `--headers-file` flags
  (global and per-domain-scoped session headers). `--accounts-file` is
  now the only session mechanism for these commands -- see
  `docs/BACKLINK_GUIDE.md#account-scoped-sessions-accounts-file`.
  `BacklinkCheckConfig`/`BacklinkChainConfig` no longer have
  `extra_headers`/`domain_headers` fields; `examples/bl-check-headers.txt`
  removed.

### Added

- `bl-check`/`bl-chain --config` -- read presets (domain, accounts_file,
  concurrency, timeout, exact, strict, user_agent, browser, headed,
  nav_timeout, render_wait, label, database) from `redirecthunter.yaml`'s
  new `bl_check:`/`bl_chain:` sections, same auto-discovery as `scan`
  already has. Priority: CLI flag > config file > built-in default.
- Multi-target per-row override for `bl-check`/`bl-chain` input files: a
  row's `|target` field (TXT), `target` column (CSV), or `"target"` key
  (JSON) may now be a `;`-separated list --
  `https://blog.example.com/post|medilana.co.id;form.medilana.com` --
  and the row is confirmed as a match if it links to *any* of the listed
  targets. A single target (no `;`) behaves exactly as before. Blank or
  whitespace-only entries (a stray `;;` or trailing `;`) are dropped;
  a row where every entry is blank falls back to `-d/--domain` as if it
  had no override at all. See `docs/CLI_REFERENCE.md`'s `bl-check`
  section and `examples/backlink.txt`'s last line for the exact syntax.
- `bl-check --browser` — render with a real headless Chromium (Playwright)
  instead of a plain HTTP GET, for pages whose links are added by
  client-side JS after load (SPAs: YouTube, Disqus, X/Twitter, Instagram,
  Threads, ...). Needs the new `redirecthunter[js]` extra
  (`pip install "redirecthunter[js]"` then `playwright install chromium`)
  — the base package still never requires Playwright. `-c/--concurrency`'s
  default resolves to `4` instead of `8` automatically in this mode (real
  page loads are much heavier than HTTP requests); override with `-c` if
  you want something else. `--headed` shows the browser window instead of
  running headless (debugging convenience; rejected if passed without
  `--browser`). `--nav-timeout`/`--render-wait` control navigation and
  post-load network-idle wait, browser mode only.
- Every `bl-check` result (either mode) now also records `robots_meta`
  (the page's `<meta name="robots" content="...">`) and `robots_header`
  (the `X-Robots-Tag` response header) — kept as two separate columns
  rather than merged, since the two can disagree (the header takes
  precedence per Google's own spec) and merging would hide that. Both
  are visible via `bl-export`'s CSV/JSON output and `docs/DATABASE_SCHEMA.md`.
  `target`/`rel` (the matched anchor's attributes, e.g. `target="_blank"`)
  were already captured — only the robots signal is new.

### Removed

- `backlink_checker.py`/`backlink_checker_js.py` (previously in
  `scripts/`) and `requirements-js.txt`. Both of their original reasons
  to exist separately from `bl-check` -- no database persistence, and
  no JS-rendering support in `bl-check` -- no longer hold now that
  `bl-check --browser` covers both, so the two scripts were retired
  rather than kept as a second, divergent way to do the same thing. Their
  matching-logic coverage (`tests/test_backlink_checker.py`) was ported
  into `tests/test_backlink.py`, alongside new tests that exercise
  `--browser` mode against a real local HTTP server with a real headless
  Chromium (not mocked). See `MEMORY.md` for the full reasoning trail,
  including why the two earlier "keep them separate" decisions were
  correct at the time and what specifically changed to supersede them.

- `bl-check` — check every URL in a file for a genuine outbound link to a
  target domain (`redirecthunter bl-check backlinks.txt -d medilana.id`),
  reusing the matching logic `backlink_checker.py`/`backlink_checker_js.py`
  already used, but now persisting the run's config and every per-URL
  result to `redirecthunter.db` (see
  `docs/DATABASE_SCHEMA.md#backlink-check-tables`) so a run can be
  revisited later instead of re-run. Distinguishes a confirmed link
  (`<a href>` pointing at the domain, or a subdomain of it) from a
  weaker indirect match (the domain embedded in a tracker/redirector
  URL), a text-only mention (never linked), and pages that couldn't be
  evaluated at all (anti-scraping block, Cloudflare challenge, login
  wall) — see `redirecthunter/backlink.py`.
- `bl-stats` — aggregate statistics for one or all recorded backlink-check
  runs (confirmed/indirect/text-mention-only/not-found/blocked/
  requires-login/error counts). Mirrors `crawl-stats`.
- `bl-show` — display individual per-URL backlink-check results in a
  table, with `--confirmed`/`--type`/`--limit` filters. Mirrors
  `crawl-show --type links`.
- `bl-export` — export a backlink-check run's per-URL results to CSV or
  JSON, with `--confirmed`. Mirrors `crawl-export`.
- `rh` — short alias for the `redirecthunter` binary (`[project.scripts]`
  now registers both, same entry point). `rh scan ...`/`rh crawl ...` work
  identically to the full name; docs continue to use `redirecthunter` for
  clarity.

### Internal

- Added `redirecthunter/run.py`'s `RunLifecycle` — a generic
  create/exists/get_config/update_status/resolve_id/delete
  implementation for one run kind's lifecycle table, parameterized by
  table name, id column, and per-kind extra columns. `crawl` and
  `backlink_check` now delegate their lifecycle methods to it instead of
  each hand-duplicating the same seven-method shape (`scan` isn't
  migrated yet — see `CONTEXT.md`'s "Run" entry for why). Collapsed the
  three byte-identical `ScanStatus`/`CrawlStatus`/`BacklinkCheckStatus`
  enums into one shared `RunStatus` as part of the same cleanup. No
  behavior change — every existing test continues to pass unmodified as
  the regression guard, and on-disk schema/`redirecthunter.db` files are
  untouched.
- Extracted `BACKLINK_RESULT_COLUMNS` into `redirecthunter/backlink.py`,
  the shared 13-column CSV/JSON header order `backlink_checker.py`'s
  `write_csv()` and the CLI's `bl-export` had each hand-written
  identically. Only the column *names* are shared — each caller keeps
  its own row-*value* mapping (`write_csv` still writes raw `True`/
  `False` for boolean columns; `bl-export` still writes `"yes"`/`"no"`),
  so no CSV output changes for either tool. See `MEMORY.md` for why that
  divergence was left alone rather than unified here.
- Moved `backlink_checker.py`/`backlink_checker_js.py` from the repo
  root into `scripts/` (root-directory tidiness, on request). Both
  scripts' behavior, dependencies, and CLI are completely unchanged —
  `pyproject.toml`'s pytest config gained `pythonpath = ["scripts"]` so
  `from backlink_checker import ...` keeps resolving the same way for
  both the test suite and `backlink_checker_js.py`'s own cross-import.
  See `MEMORY.md` for why they weren't merged into `bl-check` instead
  (both have real capability/workflow differences from it).

- Added the missing `redirecthunter/__init__.py` (the top-level package
  itself had none, though every subpackage did), which was causing
  `mypy redirecthunter` to fail outright with a module-identity collision
  (`Source file found twice under different module names`) before it
  could type-check anything. With that fixed, mypy caught one genuine
  `strict`-mode type error in `loader.py`'s CSV per-row metadata handling
  (a dict inferred as `dict[str, str]` that also needs to hold the
  `;`-split target tuple from `multi-target-per-row`), fixed with an
  explicit `dict[str, str | tuple[str, ...]]` annotation. Also fixed one
  unrelated `ruff` import-sort error in `tests/test_cli.py`
  (reformat-only). No runtime behavior change; see `MEMORY.md` for the
  full root-cause writeup.

### Fixed

- `crawl-show` — fixed a crash (`sqlite3.ProgrammingError: Cannot operate
  on a closed database`, surfaced as an unretrieved task exception after
  the results table printed) when `--type pages` or `--type links` hit
  `--limit` before the underlying result set was exhausted. Both branches
  now wrap their `iter_crawl_pages()`/`iter_crawl_links()` call in
  `contextlib.aclosing(...)`, matching the pattern `show` already used.
- `bl-export` — `_backlink_result_to_row()` (the CSV/JSON row builder)
  was missing `matched_target`, one of the 16 columns declared in
  `BACKLINK_RESULT_COLUMNS`. Every column after `target` was silently
  shifted left by one in CSV output (e.g. `blocked`'s value ended up
  under the `matched_target` header), and `-f json` raised
  `ValueError: zip() argument 2 is shorter than argument 1` and refused
  to export at all, since its row/column zip uses `strict=True`. Found
  via `tests/test_cli.py::test_bl_stats_and_show_and_export_full_workflow`,
  whose JSON-export step exposed the mismatch immediately; the CSV step
  earlier in the same test didn't fail because `csv.writer` doesn't
  enforce row/header length equality. Fixed by adding
  `result.matched_target or ""` at the correct position in the row list.
- `matched_target` persistence — a separate, more fundamental bug from
  the `bl-export` one above: `matched_target` was never part of the
  `backlink_results` schema at all, so `save_backlink_result()` had
  nothing to write it into and `_row_to_backlink_result()` had nothing
  to read it back from. This wasn't a shift like the export bug — the
  value `backlink.py` computes (including `bl-chain`/multi-target
  `bl-check` results, which record *which* member of the target
  `frozenset` matched) was silently dropped the moment a result was
  saved to `redirecthunter.db`, and stayed `None` on every subsequent
  read for the rest of that run and any later `bl-show`/`bl-export`
  against the stored data. Fixed by adding `matched_target TEXT` to
  `backlink_results` (same additive `PRAGMA table_info` + `ALTER TABLE
  ADD COLUMN` migration pattern `robots_meta`/`robots_header` used),
  wiring it through `save_backlink_result()`/`_row_to_backlink_result()`,
  and adding it to `bl-show`'s output (between "Matched href" and
  "Rel"). See `docs/DATABASE_SCHEMA.md` for the column reference.

### Added

- `crawl` — new site-wide crawler, the "audit like Ahrefs Site Audit"
  feature: starting from either a single seed URL (`redirecthunter crawl
  https://example.com`, discovering pages by following internal links) or
  an existing URL-list file (`--input-file urls.txt`, same TXT/CSV/JSON
  formats `scan` reads), it fetches every in-scope page, flags on-page SEO
  issues (missing/duplicate title, missing/too-long meta description,
  missing/multiple H1), and checks every link found — internal or
  external — for broken (4xx/5xx/transport-error) status. Bounded by
  `--max-depth`/`--max-pages`; `--no-follow-links` audits just the given
  seed(s) without discovering more; `--no-check-external-links` skips
  requesting external links entirely. Results land in three new tables in
  the same `redirecthunter.db` (see `docs/DATABASE_SCHEMA.md#crawl-mode-tables`).
- `crawl-stats` — aggregate statistics for one or all recorded crawls
  (pages crawled/alive/dead, links checked/broken, missing/duplicate
  title/meta description, missing/multiple H1). Mirrors `stats`.
- `crawl-export` — export a crawl's pages (on-page SEO audit) or checked
  links (broken-link report) to CSV or JSON, with `--broken-only`.
- `crawl-show` — display individual crawled pages or checked links in a
  table, with `--issues-only`/`--broken-only` filters. Mirrors `show`.
- `backlink_checker.py` (root-level standalone script, separate from the
  `redirecthunter` package): verifies a target domain appears as a real
  outbound `<a href>` in a page's body — the backlink-audit question, as
  opposed to `scan`'s redirect-audit question. Whole-hostname matching
  (never substring), `final_url_is_target` for short-link redirects that
  land straight on the target, `indirect_query` for tracker/redirector-
  embedded matches, `text_mention_only` for unlinked plain-text mentions,
  `blocked` for anti-bot responses (bot-challenge pages, LinkedIn's
  non-standard `999` status), and `requires_login` for auth-wall
  redirects (e.g. Facebook). See the README's "Backlink verification"
  section.
- `backlink_checker_js.py`: same backlink-audit question as
  `backlink_checker.py`, rendered through a real headless Chromium via
  Playwright instead of raw `httpx`. For JavaScript single-page apps
  (YouTube, Disqus, X/Twitter, Instagram, Threads) where the real DOM is
  built by client-side JS after load — `backlink_checker.py` can only see
  the raw pre-JS HTML and structurally cannot see those links. Imports
  its matching/output logic directly from `backlink_checker.py` (never
  duplicated) so results from both scripts are directly comparable.
  Playwright is an opt-in dependency (`requirements-js.txt`), not part of
  `requirements.txt`.
- `--status-code` filter on `export` and `show` — restricts results to an
  exact HTTP status code (`--status-code 301`) and/or a whole response
  class (`--status-code 3xx`). Repeatable and comma-separated
  (`--status-code 301,302 --status-code 4xx`); combines with the existing
  `--*-only` filters.
- `rel` / `target` anchor attributes are now captured everywhere a link is
  discovered: `backlink_checker.py`/`backlink_checker_js.py`'s
  `BacklinkResult.rel`/`.target` (CSV column `target` added alongside the
  existing `rel`), and `crawl`'s `CrawlLinkResult.rel`/`.target_attr`
  (new `rel`/`target_attr` columns on the `crawl_links` table, surfaced by
  `crawl-export --type links`). Existing SQLite databases are upgraded via
  `ALTER TABLE` on connect, same as the earlier `body_link` migration.

### Changed

- `scan` now prints an explicit warning when running with the default
  `--method HEAD`, calling out that meta-refresh, JavaScript-redirect,
  and `body_link` (`--has-link-only`) detection all require a response
  body and will be skipped. Previously this limitation was documented
  only in the README FAQ, which meant it was easy to run a full HEAD
  scan and only discover afterward that `--has-link-only` always exports
  zero rows.
- `--has-link-only` help text (on both `export` and `show`) now states
  outright that it requires a `--method GET` scan.

### Internal

- Removed a full duplicate copy of every core module (`cli.py`,
  `exporter.py`, `models.py`, etc., plus a duplicate `plugins/`) that had
  been sitting loose at the repo root alongside the real `redirecthunter/`
  package since before it was packaged. Confirmed nothing imported them,
  then deleted — they were dead weight and had already drifted out of
  sync with the real package (the root copies were missing this release's
  `--status-code` work).
- `exporter.py` split into an `export/` subpackage (`filters.py`,
  `csv_writer.py`, `json_writer.py`, `service.py`), mirroring the
  existing `plugins/` layout. Public API (`Exporter`, `ExportFilter`,
  `ExportError`, `CSV_COLUMNS`) is unchanged in shape, only its import
  path moved: `redirecthunter.exporter` → `redirecthunter.export`.
  `tests/test_exporter.py` renamed to `tests/test_export.py` to match.
  See `docs/ARCHITECTURE.md#the-export-subpackage` for the module
  breakdown.

## [1.1.0]

### Added

- `redirecthunter redact-target` — replaces `examples/url-target-replace.sh`;
  domain → `{TARGET}` token replacement, moved into the tested Python CLI.
- `redirecthunter expand-target` — new: `{TARGET}` → real target URL,
  built on the existing `expand_target()` helper, without requiring a full
  `scan`.

### Removed

- `examples/url-target-replace.sh` — retired now that both commands above
  have shipped. See `examples/README.md` for the migration note.

## [1.0.0]

Initial stable release.

### Added

- `scan` — run a redirect-discovery scan against a candidate-URL input
  file (TXT/CSV/JSON/SQLite), with configurable method, concurrency,
  retries, rate limiting, proxying, and TLS verification.
- `resume` — continue an interrupted scan from where it left off.
- `stats` — aggregate statistics for one or all recorded scans.
- `export` — export a scan's results to CSV, JSON, or SQLite.
- `show` — display individual results in a table, with filters.
- `find` — search across recorded results.
- `delete` — remove a recorded scan.
- `vacuum` — reclaim space in the results database.
- Redirect-type plugin pipeline: HTTP `Location` header, meta-refresh, and
  static JavaScript-redirect pattern matching, plus independent Cloudflare
  classification.
- `{TARGET}` placeholder support in candidate-URL templates, expanded at
  scan time via `--target`.
