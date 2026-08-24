# CLI Reference

Every flag documented here was captured from `--help` output of the actual
implemented CLI (`python -m redirecthunter.cli <command> --help`), not
written from the spec independently — if this ever drifts from reality,
regenerate it from `--help` rather than editing by hand.

## Short scan IDs

Every command that takes a `SCAN_ID` accepts either the full UUID or an
**unambiguous prefix** of it — like a git short hash. `stats` (list view)
displays the first 8 characters for exactly this reason:

```bash
redirecthunter stats                    # shows Scan ID column truncated to 8 chars
redirecthunter show 3f9a1c2e            # 8-char prefix works directly
redirecthunter find 3f9a1c2e --output external.txt
```

If a prefix matches more than one scan, the command reports the
ambiguity and asks for more characters — it never silently guesses.

## `scan`

Run a new redirect-discovery scan against a candidate URL input file.

```
redirecthunter scan [OPTIONS] INPUT_FILE
```

**Argument**

| | |
|---|---|
| `INPUT_FILE` | Candidate URL input file (`.txt`, `.csv`, `.json`, `.db`/`.sqlite`). Required. |

**Input options**

| Flag | Default | Description |
|---|---|---|
| `--format` | inferred from extension | Input format: `txt`, `csv`, `json`, `sqlite`. |
| `--input-table` | `urls` | Table name for SQLite input. |
| `--input-column` | `url` | URL column name for CSV/SQLite input. |
| `--target` | none | Replacement value for `{TARGET}` in candidate URL templates. |

**Request options**

| Flag | Default | Description |
|---|---|---|
| `--method` | `HEAD` | `HEAD` or `GET`. GET is required to detect meta-refresh/JS redirects and to populate `body_link` (`--has-link-only`) — they all need a response body. Running `scan` with the default HEAD now prints a warning at start time reminding you of this. |
| `--follow-redirects` / `--no-follow-redirects` | follow | Follow the full chain, or inspect only the first hop. |
| `--max-redirects` | `10` | Maximum redirects followed per URL. |
| `--http2` / `--no-http2` | enabled | Enable HTTP/2. |
| `--proxy` | none | Proxy URL, e.g. `socks5://127.0.0.1:1080` or `http://proxy:8080`. |
| `--user-agent` | `RedirectHunter/1.0` | Custom `User-Agent` header. |
| `--header` / `-H` | none | Extra header as `'Name: Value'`. Repeatable. |
| `--verify-tls` / `--insecure` | verify | Verify TLS certificates. |

**Performance & reliability**

| Flag | Default | Description |
|---|---|---|
| `--workers` | `100` | Concurrent worker count (1–2000). |
| `--timeout` | `10` | Per-request total timeout, seconds. |
| `--connect-timeout` | `5` | Per-request connect timeout, seconds. |
| `--retry` | `2` | Retries on transport-level failure. |
| `--retry-backoff` | `0.5` | Base exponential backoff delay, seconds. |
| `--rate-limit` | unlimited | Max aggregate requests/second across all workers. |

**Storage & config**

| Flag | Default | Description |
|---|---|---|
| `--database` / `--db` | `redirecthunter.db` | SQLite results database path. |
| `--config` | auto-discovered | YAML config file. See [example](../examples/redirecthunter.yaml). |
| `--label` | none | Human-readable label for this scan. |

**Logging**

| Flag | Default | Description |
|---|---|---|
| `--log-level` | `INFO` | `DEBUG`, `INFO`, or `ERROR`. |
| `--log-file` | none | Also write logs to this file. |
| `--quiet` / `-q` | off | Suppress console log output. |

**Examples**

```bash
redirecthunter scan urls.txt
redirecthunter scan urls.txt --target https://example.org
redirecthunter scan urls.txt --workers 500 --method GET
redirecthunter scan urls.txt --proxy socks5://127.0.0.1:1080
redirecthunter scan urls.csv --input-column redirect_url --target https://example.org
redirecthunter scan urls.txt --config redirecthunter.yaml --label "Q3 audit"
```

## `resume`

Resume a previously interrupted scan from where it left off.

```
redirecthunter resume [OPTIONS] SCAN_ID
```

Rebuilds the exact configuration used by the original `scan` invocation
(input file, target, method, headers, retry settings, everything) from the
`config_json` snapshot stored in the database, and skips every candidate
URL that already has a recorded result. Only a small subset of
performance-related flags may be overridden for the resumed run:

| Flag | Description |
|---|---|
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |
| `--workers` | Override worker count for the resumed run. |
| `--timeout` | Override per-request timeout. |
| `--rate-limit` | Override rate limit. |
| `--log-level`, `--log-file`, `--quiet` | Same as `scan`. |

**Example**

```bash
redirecthunter resume 3f9a1c2e-4b7d-4a1e-9c3f-1a2b3c4d5e6f --database redirecthunter.db
```

## `stats`

Show aggregate statistics for one scan, or list all recorded scans.

```
redirecthunter stats [OPTIONS] [SCAN_ID]
```

| | |
|---|---|
| `SCAN_ID` | Optional. Omit to list every scan in the database as a table. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |

**Examples**

```bash
redirecthunter stats                              # list all scans
redirecthunter stats 3f9a1c2e-4b7d-4a1e-9c3f-...   # one scan's summary
```

## `export`

Export a scan's results to CSV, JSON, or a standalone SQLite file.

```
redirecthunter export [OPTIONS] SCAN_ID OUTPUT
```

| | |
|---|---|
| `SCAN_ID` | Required. |
| `OUTPUT` | Required. Output file path. |
| `--format` | `csv` (default), `json`, or `sqlite`. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |
| `--alive-only` | off | Export only results where the target responded. |
| `--redirects-only` | off | Export only results where a redirect was detected. |
| `--cloudflare-only` | off | Export only Cloudflare-protected results. |
| `--has-link-only` | off | Export only results whose terminal page body has a navigable `<a href>` link. Requires the scan to have been run with `--method GET` — HEAD scans always give 0 rows here. |
| `--status-code` | none | Repeatable, comma-separated. Exact code (`301`) and/or class (`3xx`); any match passes. |

The `--*-only` filters (including `--status-code`) can be combined — a
row must pass all of them to be exported — but they're only supported
for `--format csv/json`; `--format sqlite` always exports every result
(export unfiltered, then query the file directly, or use CSV/JSON).

**Examples**

```bash
redirecthunter export 3f9a1c2e-... results.csv
redirecthunter export 3f9a1c2e-... results.json --format json
redirecthunter export 3f9a1c2e-... results.db --format sqlite
redirecthunter export 3f9a1c2e-... redirects.csv --redirects-only
redirecthunter export 3f9a1c2e-... links.csv --has-link-only
redirecthunter export 3f9a1c2e-... redirects_301.csv --status-code 301
redirecthunter export 3f9a1c2e-... redirects_broken.csv --status-code 4xx,5xx
```

See [`DATABASE_SCHEMA.md`](./DATABASE_SCHEMA.md) for exactly what each
export format contains and what a `--format sqlite` export preserves
(all four tables, including `headers`).

## `delete`

Permanently delete a scan and every row that belongs to it (`results`,
`chain`, `headers` — all cascaded automatically via foreign keys).

```
redirecthunter delete [OPTIONS] SCAN_ID
```

| | |
|---|---|
| `SCAN_ID` | Required. Full or short prefix. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |
| `--yes` / `-y` | Skip the confirmation prompt. |
| `--vacuum` | Reclaim disk space immediately after deleting (see `vacuum` below). |

This cannot be undone. Without `--yes`, the command shows the scan's
label and result count, then asks for confirmation.

**Examples**

```bash
redirecthunter delete 3f9a1c2e
redirecthunter delete 3f9a1c2e --yes --vacuum
```

## `vacuum`

Reclaim disk space freed by previously deleted scans.

```
redirecthunter vacuum [OPTIONS]
```

| | |
|---|---|
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |

SQLite never shrinks a database file automatically after a `DELETE` — the
freed pages just become available for future writes within the same
file. `vacuum` rewrites the entire file compactly, which is the only way
to actually reduce the file size on disk. It needs roughly as much free
disk space as the database itself and can take a while on large
databases, which is why it's a separate, explicit step rather than
something `delete` does by default.

**Example**

```bash
redirecthunter vacuum --database redirecthunter.db
```

## `show`

Display individual results for a scan in a Rich table, with optional filters.

```
redirecthunter show [OPTIONS] SCAN_ID
```

| | |
|---|---|
| `SCAN_ID` | Required. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |
| `--limit` | `50` | Maximum results to display. |
| `--alive-only` | off | Show only responsive targets. |
| `--redirects-only` | off | Show only results where a redirect was detected. |
| `--cloudflare-only` | off | Show only Cloudflare-protected results. |
| `--has-link-only` | off | Show only results whose terminal page body has a navigable `<a href>` link. Requires the scan to have been run with `--method GET` — HEAD scans always give 0 rows here. |
| `--status-code` | none | Repeatable, comma-separated. Exact code (`301`) and/or class (`3xx`); any match passes. |

**Examples**

```bash
redirecthunter show 3f9a1c2e-...
redirecthunter show 3f9a1c2e-... --redirects-only --limit 100
redirecthunter show 3f9a1c2e-... --cloudflare-only
redirecthunter show 3f9a1c2e-... --has-link-only
redirecthunter show 3f9a1c2e-... --status-code 301,302,404
```

## `find`

Find redirects whose destination is **outside** a given domain — the core
open-redirect audit question: does this endpoint actually send visitors
somewhere unintended? Add `--invert` to flip the question around: which
endpoints **correctly** redirect to your domain?

```
redirecthunter find [OPTIONS] SCAN_ID
```

| | |
|---|---|
| `SCAN_ID` | Required. Full or short prefix. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |
| `--external-domain` / `--domain` | Domain to compare against. Default: auto-detected from the scan's `--target`. |
| `--invert` | Show redirects that **match** the domain instead of ones outside it. |
| `--output` / `-o` | Save results as a plain list (one entry per line, nothing else) to this file, instead of printing a table. |
| `--field` | With `--output`: `destination` (default), `source`, or `both` (`source_url -> destination` per line). |
| `--limit` | `50` | Maximum rows shown in the terminal table (does not apply to `--output`). |

Domain matching is hostname-based, not substring-based — `sub.example.org`
is correctly recognized as belonging to `example.org`, while
`notexample.org.evil.com` is correctly recognized as *not* belonging to it
despite containing the string `example.org`.

The destination checked is the true redirect target even for
`--no-follow-redirects` scans: it uses the terminal response's `final_url`
when a chain was followed, and falls back to the resolved `Location`
header when it wasn't (otherwise the "destination" would just be the
request's own URL).

**Why `--invert` matters in practice:** many public ad-click/redirect/link
services don't reliably forward to the URL you give them — some check
`User-Agent`/`Referer` and only redirect real browsers, others silently
fall back to an unrelated default page. `--invert` filters a large
candidate list down to only the endpoints *confirmed*, by an actual
request, to redirect where you expect.

**Examples**

```bash
# Domain auto-detected from the scan's --target
redirecthunter find 3f9a1c2e

# Save the plain destination list to a file
redirecthunter find 3f9a1c2e --output external_redirects.txt

# Which source URLs actually, correctly redirect to the target?
redirecthunter find 3f9a1c2e --invert --field source --output confirmed_backlinks.txt

# Explicit domain, with source URLs included alongside destinations
redirecthunter find 3f9a1c2e --domain example.org --output out.txt --field both
```

## `redact-target`

Replace occurrences of a domain with a token (`{TARGET}` by default) across
a plain-text file of URLs — the tested-in-Python replacement for the
retired `examples/url-target-replace.sh` script. Lines with no domain match
are written through unchanged; no data is silently dropped.

```
redirecthunter redact-target [OPTIONS] INPUT_FILE
```

| | |
|---|---|
| `INPUT_FILE` | Plain-text file of URLs, one per line. Required. |
| `--domain` / `-d` | Domain to search for and replace. Required. |
| `--output` / `-o` | Output file path. Default: stdout. Required when `--format` is `sqlite`. |
| `--format` / `-f` | Output format: `txt`, `csv`, `json`, or `sqlite`. Inferred from `-o`'s extension if omitted, else `txt`. |
| `--token` / `-t` | Replacement token. Default: `{TARGET}`. |
| `--verbose` / `-v` | Report each unmatched line and the total unmatched count to stderr. |

Unlike every other RedirectHunter command, `redact-target` and
`expand-target` carry short-flag aliases (`-d`/`-o`/`-f`/`-t`/`-v`) — kept
for muscle-memory compatibility with the shell-script workflow they
replace (see `MEMORY.md` for the full rationale).

**Examples**

```bash
redirecthunter redact-target urls.txt -d medilana.id
redirecthunter redact-target urls.txt -d medilana.id -o out.csv
redirecthunter redact-target urls.txt -d medilana.id -o out.json --format json
redirecthunter redact-target urls.txt -d medilana.id -o out.db --format sqlite
redirecthunter redact-target urls.txt -d medilana.id --verbose > out.txt
```

## `expand-target`

The reverse of `redact-target`: expand `{TARGET}` templates in a file into
real, ready-to-request URLs against a chosen target — without running a
full `scan`. Always plain-text in, plain-text out. Lines with no
`{TARGET}` token are written through unchanged.

```
redirecthunter expand-target [OPTIONS] INPUT_FILE
```

| | |
|---|---|
| `INPUT_FILE` | Plain-text file of `{TARGET}`-templated URLs, one per line. Required. |
| `--target` | Replacement value substituted for `{TARGET}`. Required. |
| `--output` / `-o` | Output file path. Default: stdout. |
| `--encode` | Percent-encode `--target`'s value before substitution. |
| `--verbose` / `-v` | Report each line with no `{TARGET}` token and the total such count to stderr. |

**Examples**

```bash
redirecthunter expand-target templates.txt --target https://example.org
redirecthunter expand-target templates.txt --target https://example.org --encode
redirecthunter expand-target templates.txt --target https://example.org -o out.txt --verbose
```

## `crawl`

Crawl a site (or a fixed list of pages), discovering broken links and
on-page SEO issues. Two seed modes:

```
redirecthunter crawl [OPTIONS] [SEED]
```

| | |
|---|---|
| `SEED` | Seed URL for domain mode (discovers pages by following internal links). Omit if using `--input-file`. |
| `--input-file` | Seed from every URL in this file (TXT/CSV/JSON, same formats as `scan`) instead of one domain. |
| `--format` | `--input-file` format. Inferred from extension if omitted. |
| `--input-column` | URL column name for CSV input. Default `url`. |
| `--allowed-domain` | Repeatable. Extra hostname treated as internal scope besides the seed's own host. |
| `--max-depth` | Maximum link-hops from a seed to still crawl as a page. Default `3`. |
| `--max-pages` | Maximum number of pages to fetch and fully audit. Default `500`. |
| `--follow-links` / `--no-follow-links` | Discover and crawl further pages from links found on each page. Default on. `--no-follow-links` audits only the given seed(s), still checking (not crawling) their links. |
| `--check-external-links` / `--no-check-external-links` | Request external links to detect broken ones. Default on. |
| `--include-query-string` / `--no-include-query-string` | Treat URLs differing only by query string as different pages. Default on. |
| `--workers` | Concurrent crawl workers. Default `20`. |
| `--timeout`, `--connect-timeout`, `--retry`, `--retry-backoff`, `--rate-limit`, `--http2`/`--no-http2`, `--proxy`, `--user-agent`, `--header`, `--verify-tls`/`--no-verify-tls` | Same meaning as the equivalent `scan` flags. |
| `--title-min-length` / `--title-max-length` | Title length thresholds for the `title_too_short`/`title_too_long` issue flags. Defaults `10`/`60`. |
| `--meta-description-max-length` | Meta description length threshold. Default `160`. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |
| `--label` | Human-readable label for this crawl. |

**Examples**

```bash
redirecthunter crawl https://example.com
redirecthunter crawl https://example.com --max-depth 2 --max-pages 200
redirecthunter crawl --input-file urls.txt --no-follow-links
redirecthunter crawl https://example.com --no-check-external-links --workers 50
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md#crawl-mode) for how the two
seed modes and the page/link split actually work.

## `crawl-stats`

Aggregate statistics for one crawl, or a listing of every recorded crawl.
Mirrors `stats`.

```
redirecthunter crawl-stats [CRAWL_ID] [OPTIONS]
```

| | |
|---|---|
| `CRAWL_ID` | Optional. Full or short prefix. Omit to list all crawls. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |

## `crawl-export`

Export a crawl's pages or checked links to CSV or JSON.

```
redirecthunter crawl-export [OPTIONS] CRAWL_ID OUTPUT
```

| | |
|---|---|
| `CRAWL_ID` | Required. Full or short prefix. |
| `OUTPUT` | Required. Output file path. |
| `--type` | `pages` (default, on-page SEO audit) or `links` (broken-link report). |
| `--format` | `csv` (default) or `json`. `sqlite` is not supported. |
| `--broken-only` | For `--type links`: only broken links. For `--type pages`: only pages that link to something broken. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |

**Examples**

```bash
redirecthunter crawl-export 3f9a1c2e pages.csv
redirecthunter crawl-export 3f9a1c2e broken_links.csv --type links --broken-only
redirecthunter crawl-export 3f9a1c2e pages.json --format json
```

## `crawl-show`

Display individual crawled pages or checked links in a table. Mirrors `show`.

```
redirecthunter crawl-show [OPTIONS] CRAWL_ID
```

| | |
|---|---|
| `CRAWL_ID` | Required. Full or short prefix. |
| `--type` | `pages` (default) or `links`. |
| `--broken-only` | For `--type links`, only show broken links. |
| `--issues-only` | For `--type pages`, only show pages with at least one on-page SEO issue. |
| `--limit` | Maximum rows to display. Default `50`. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |

## `bl-check`

Check every URL in a file for a genuine outbound link to a target domain
— answers a different question from `scan` (does the page's rendered HTML
actually *link to* the domain, not whether a URL *redirects to* it),
persisting the run and every per-URL result to `redirecthunter.db` so it
can be revisited with `bl-stats`/`bl-show`/`bl-export`. Every result also
records the matched link's `target`/`rel` attributes and the page's
robots signal (`<meta name="robots">` and the `X-Robots-Tag` header —
kept separately since the two can disagree), all visible via `bl-export`.

By default this fetches with a plain HTTP GET (`httpx`), which never
executes JavaScript. Pass `--browser` to render with a real headless
Chromium (Playwright) instead, for pages whose links are added by
client-side JS after load — needs the `redirecthunter[js]` extra
(`pip install "redirecthunter[js]"` then `playwright install chromium`).

This command family uses short flags (`-d`, `-c`, `-t`, `-u`, `-l`, `-f`,
`-o`) as a deliberate, documented exception to the rest of this CLI's
long-flag-only convention — see `AGENT.md`.

**Per-row target override.** Any row can pin its own target instead of
using the run's `-d/--domain` default: append `|target` to a TXT line
(`https://blog.example.com/post|medilana.com`), or fill a `target`
column (CSV) / `"target"` key (JSON) for that row. A row with no
override keeps using `-d/--domain` as before.

A row can also list *several* acceptable targets, separated by `;`:

```
https://blog.example.com/post|medilana.co.id;form.medilana.com;img.medilana.my.id
```

That row is confirmed as a match if the page links to *any* of the
three listed targets — useful for a placement that might legitimately
link to a brand's main domain, a subdomain, or a country-variant
domain. `bl-show`'s `matched_target` column reports which one actually
matched. Whitespace around each `;`-separated entry is trimmed, and a
blank entry (from a stray `;;` or a trailing `;`) is dropped rather than
treated as a target — a row where every entry is blank is treated as
having no override at all, falling back to `-d/--domain`.

```
redirecthunter bl-check [OPTIONS] INPUT_FILE
```

| | |
|---|---|
| `INPUT_FILE` | Required. TXT/CSV/JSON/SQLite file of URLs to check — same formats `scan`/`crawl --input-file` accept. |
| `--domain` / `-d` | Target domain to look for, e.g. `medilana.id`. Required unless set as `bl_check.domain` in `redirecthunter.yaml` — see `--config`. |
| `--format` | Input file format. Inferred from extension if omitted. |
| `--input-column` | URL column name for CSV input. Default `url`. |
| `--concurrency` / `-c` | Concurrent workers. Default `8` (httpx mode) or `4` (`--browser` mode, real page loads are much heavier — override with `-c` if you want something else). |
| `--timeout` / `-t` | Per-request timeout, seconds. httpx mode only. Default `15.0`. |
| `--exact` | Match the domain exactly — do not count subdomains (`blog.medilana.id`) as a match. |
| `--strict` | Skip weaker/indirect (tracker-embedded) match signals. |
| `--agent` / `-u` | `User-Agent` header sent with every request. |
| `--accounts-file` | Registry of per-account session headers (`account_id|Name: Value` per line), paired with `account_id|URL` input rows — for one domain needing many different sessions, one per row. See [Account-scoped sessions](./BACKLINK_GUIDE.md#account-scoped-sessions-accounts-file). |
| `--browser` | Render with a real (Playwright) browser instead of a plain HTTP GET. Needs the `redirecthunter[js]` extra installed separately. |
| `--headed` | Show the real browser window instead of running headless. Only meaningful with `--browser`; rejected otherwise. |
| `--nav-timeout` | Seconds to wait for navigation. Only used with `--browser`. Default `30.0`. |
| `--render-wait` | Seconds to wait for the page to go network-idle after load, so a SPA's own JS has time to hydrate the DOM. Only used with `--browser`. Default `8.0`. |
| `--label` / `-l` | Human-readable label for this run. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |
| `--config` | YAML config file. Auto-discovered (`redirecthunter.yaml` et al.) if omitted. Presets read from its `bl_check:` section — domain, accounts_file, concurrency, timeout, exact, strict, user_agent, browser, headed, nav_timeout, render_wait, label, database. Priority: CLI flag > config file > built-in default. See [Presets](./BACKLINK_GUIDE.md#presets---config). |
| `--log-level`, `--log-file`, `--quiet` / `-q` | Same meaning as the equivalent `scan` flags. |

**Examples**

```bash
redirecthunter bl-check examples/tier1.txt -d medilana.id
redirecthunter bl-check backlinks.csv -d medilana.id -c 16 -t 20 --exact
redirecthunter bl-check backlinks.txt -d medilana.id --strict -l "Q3 audit"
redirecthunter bl-check backlinks.txt -d medilana.id --browser
redirecthunter bl-check backlinks.txt -d medilana.id --browser --headed --nav-timeout 60
redirecthunter bl-check backlinks.txt --config redirecthunter.yaml
```

## `bl-chain`

Check a tiered/pyramid backlink structure across two or more tier input
files in one run — tier 1 is checked against `-d/--domain` exactly like
`bl-check`; tier N (N > 1) is checked against the set of hostnames
extracted from tier N-1's own input URLs by default (every row, not just
the ones tier N-1 confirmed — pass `--require-confirmed-parent` for the
stricter, confirmed-only derivation instead). A row's own per-row
`|target` override (same syntax as `bl-check`, see above) still wins over
either derived default, within any tier.

Each tier is persisted as an ordinary `bl-check` run — its own
`backlink_id`, its own rows in `backlink_results` — so `bl-stats`/
`bl-show`/`bl-export` work on any individual tier exactly like they do
for a plain `bl-check` run; there is no separate `bl-chain-stats`/
`bl-chain-show`/`bl-chain-export` (a combined per-tier summary table is
printed at the end of the `bl-chain` run itself). Tier order is always
the order tier files are given on the command line — never inferred from
filenames.

This command reuses `bl-check`'s short-flag convention (`-d`, `-c`, `-t`,
`-u`, `-l`) and its `--browser`/`--headed`/`--nav-timeout`/`--render-wait`
options, applied per tier.

```
redirecthunter bl-chain [OPTIONS] TIER_PATHS...
```

| | |
|---|---|
| `TIER_PATHS` | Required. Two or more ordered tier input files (TXT/CSV/JSON), tier 1 first. |
| `--domain` / `-d` | Root (tier 1) target domain to look for, e.g. `medilana.id`. Required unless set as `bl_chain.domain` in `redirecthunter.yaml` — see `--config`. |
| `--require-confirmed-parent` | Derive each tier's default target set only from the previous tier's confirmed (`match_found`) rows, instead of all of its input URLs. Off by default. |
| `--concurrency` / `-c` | Concurrent workers, applied per tier. Default `8` (httpx mode) or `4` (`--browser` mode). |
| `--timeout` / `-t` | Per-request timeout, seconds. httpx mode only. Default `15.0`. |
| `--exact` | Match target domains exactly — do not count subdomains as a match. |
| `--strict` | Skip weaker/indirect (tracker-embedded) match signals. |
| `--agent` / `-u` | `User-Agent` header sent with every request. |
| `--accounts-file` | Registry of per-account session headers, same as `bl-check`'s `--accounts-file` — one `--accounts-file` is shared across every tier. See [Account-scoped sessions](./BACKLINK_GUIDE.md#account-scoped-sessions-accounts-file). |
| `--browser` | Render every tier with a real (Playwright) browser instead of a plain HTTP GET. |
| `--headed` | Show the real browser window instead of running headless. Only meaningful with `--browser`. |
| `--nav-timeout` | Seconds to wait for navigation. Only used with `--browser`. Default `30.0`. |
| `--render-wait` | Seconds to wait for network-idle after load. Only used with `--browser`. Default `8.0`. |
| `--label` / `-l` | Human-readable label for this chain. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |
| `--config` | YAML config file. Auto-discovered (`redirecthunter.yaml` et al.) if omitted. Presets read from its `bl_chain:` section — domain, accounts_file, concurrency, timeout, exact, strict, user_agent, browser, headed, nav_timeout, render_wait, label, database, require_confirmed_parent. `tier_paths` is never read from YAML. Priority: CLI flag > config file > built-in default. See [Presets](./BACKLINK_GUIDE.md#presets---config). |
| `--log-level`, `--log-file`, `--quiet` / `-q` | Same meaning as the equivalent `scan` flags. |

**Examples**

```bash
redirecthunter bl-chain examples/tier1.txt examples/tier2.txt -d medilana.id --accounts-file examples/bl-check-accounts.txt
redirecthunter bl-chain examples/tier1.txt examples/tier2.txt examples/tier3.txt -d medilana.id --require-confirmed-parent --accounts-file examples/bl-check-accounts.txt
redirecthunter bl-chain tier1.txt tier2.txt -d medilana.id -c 16 --exact -l "Q3 pyramid audit"
redirecthunter bl-chain tier1.txt tier2.txt --config redirecthunter.yaml
```

`--accounts-file` is required in the first two examples above because
`examples/tier1.txt` includes one `account_id|URL` row (see
[Account-scoped sessions](./BACKLINK_GUIDE.md#account-scoped-sessions-accounts-file)) --
any referenced `account_id` missing from the registry is a hard error,
so a plain `bl-chain` run over that same tier file without
`--accounts-file` fails fast rather than silently checking that one row
unauthenticated. Note the shipped `examples/bl-check-accounts.txt` has
every account commented out (no real credentials committed), so even
with `--accounts-file` given, these two commands will still exit with a
"missing account_id" error until you fill in and uncomment a real
`account_001` entry in your own copy.

See [`BACKLINK_GUIDE.md#chained-tiered-audits-redirecthunter-bl-chain`](./BACKLINK_GUIDE.md#chained-tiered-audits-redirecthunter-bl-chain)
for the full walkthrough of why tiered audits exist and how the derived
target sets behave.

## `bl-stats`

Aggregate statistics for one backlink-check run, or a listing of every
recorded run. Mirrors `crawl-stats`.

```
redirecthunter bl-stats [BACKLINK_ID] [OPTIONS]
```

| | |
|---|---|
| `BACKLINK_ID` | Optional. Full or short prefix. Omit to list all runs. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |

**Examples**

```bash
redirecthunter bl-stats
redirecthunter bl-stats 3f9a1c2e
```

## `bl-show`

Display individual per-URL backlink-check results in a Rich table.
Mirrors `crawl-show --type links`.

```
redirecthunter bl-show [OPTIONS] BACKLINK_ID
```

| | |
|---|---|
| `BACKLINK_ID` | Required. Full or short prefix. |
| `--confirmed` | Only show URLs with a confirmed backlink match. |
| `--type` | Only show results with this `match_type` (`anchor`, `subdomain_anchor`, `final_url_is_target`, `indirect_query`, `text_mention_only`, `not_found`). |
| `--limit` | Maximum rows to display. Default `50`. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |

**Examples**

```bash
redirecthunter bl-show 3f9a1c2e
redirecthunter bl-show 3f9a1c2e --confirmed
redirecthunter bl-show 3f9a1c2e --type text_mention_only --limit 20
```

## `bl-export`

Export a backlink-check run's per-URL results to CSV or JSON. Mirrors
`crawl-export`. Deliberately its own small streaming writer (not
`Exporter`, which is `RedirectResult`-shaped) — a `BacklinkResult` has
entirely different columns. No `--format sqlite`, matching
`crawl-export`'s own documented v1 scope decision.

```
redirecthunter bl-export [OPTIONS] BACKLINK_ID
```

| | |
|---|---|
| `BACKLINK_ID` | Required. Full or short prefix. |
| `--output` / `-o` | Required. Output file path. |
| `--format` / `-f` | `csv` (default) or `json`. `sqlite` is not supported. |
| `--confirmed` | Only export URLs with a confirmed backlink match. |
| `--database` / `--db` | SQLite results database path (default `redirecthunter.db`). |

**Examples**

```bash
redirecthunter bl-export 3f9a1c2e -o report.csv
redirecthunter bl-export 3f9a1c2e -o confirmed.csv --confirmed
redirecthunter bl-export 3f9a1c2e -o report.json -f json
```

## Exit codes

All commands use standard exit codes: `0` on success (including the
"nothing to do" case for an empty input file), `1` on any handled error
(missing file, invalid config, nonexistent scan/database). Unhandled
exceptions during a scan mark it `failed` in the database before
propagating, so `redirecthunter stats <scan_id>` always reflects the true
outcome even after a crash.
