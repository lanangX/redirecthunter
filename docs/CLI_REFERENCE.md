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
| `--method` | `HEAD` | `HEAD` or `GET`. GET is required to detect meta-refresh/JS redirects (they need a response body). |
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

**Examples**

```bash
redirecthunter export 3f9a1c2e-... results.csv
redirecthunter export 3f9a1c2e-... results.json --format json
redirecthunter export 3f9a1c2e-... results.db --format sqlite
```

See [`DATABASE_SCHEMA.md`](./DATABASE_SCHEMA.md) for exactly what each
export format contains and what a `--format sqlite` export preserves
(all four tables, including `headers`).

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

**Examples**

```bash
redirecthunter show 3f9a1c2e-...
redirecthunter show 3f9a1c2e-... --redirects-only --limit 100
redirecthunter show 3f9a1c2e-... --cloudflare-only
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

## Exit codes

All commands use standard exit codes: `0` on success (including the
"nothing to do" case for an empty input file), `1` on any handled error
(missing file, invalid config, nonexistent scan/database). Unhandled
exceptions during a scan mark it `failed` in the database before
propagating, so `redirecthunter stats <scan_id>` always reflects the true
outcome even after a crash.
