# Architecture

RedirectHunter is organized as a pipeline of small, single-responsibility
modules, each independently testable and connected only through the
Pydantic models in `models.py`. This document explains how data flows
through the system and why the module boundaries are drawn where they are.

## Module map

```
redirecthunter/
├── models.py       Pydantic data contracts — the only shared vocabulary
│                    between every other module. Nothing else defines its
│                    own ad-hoc dict shape for a scan, a hop, or a result.
├── config.py        CLI flags + YAML file + defaults -> one ScanConfig
├── loader.py         Input file (TXT/CSV/JSON/SQLite) -> CandidateURL stream
├── engine.py         Worker pool, HTTP client, retries, rate limiting,
│                    the redirect-following loop
├── analyzer.py       Raw HTTP response -> RedirectHop / RedirectResult
├── detector.py        Orchestrates the redirect-detection plugin pipeline
├── plugins/            One module per detection strategy (see below)
├── fingerprint.py     Header-based server/CDN/Cloudflare classification
├── database.py         aiosqlite persistence (scan/results/chain/headers)
├── exporter.py          Streaming CSV/JSON/SQLite export
├── cli.py                Typer commands + Rich progress/tables
├── logger.py               Centralized Rich logging setup
└── utils.py                 Dependency-free helpers (URL expansion, etc.)
```

`utils.py` sits at the bottom of the import graph — nothing in it depends
on any other RedirectHunter module, so every other module can safely
depend on it without risk of a circular import.

## Data flow for one candidate URL

```
 input file           loader.py             engine.py                analyzer.py           detector.py
┌──────────┐   ┌───────────────────┐   ┌────────────────────┐   ┌───────────────────┐  ┌──────────────────┐
│ urls.txt │──▶│ CandidateURL       │──▶│ expand {TARGET}     │──▶│ analyze_hop()      │─▶│ RedirectDetector  │
│ .csv     │   │ (raw_url +         │   │ HTTP request        │   │  -> HopAnalysis     │  │  .analyze()       │
│ .json    │   │  row_metadata)     │   │ (retry/backoff,      │   │  -> next_url?       │  │  runs plugins in  │
│ .db      │   └───────────────────┘   │  rate limited)       │   └───────────────────┘  │  priority order,  │
└──────────┘                            │  loop while           │           │              │  + Cloudflare      │
                                        │  redirect found &    │           ▼              │  classification    │
                                        │  hops < max           │   build_result()          └──────────────────┘
                                        └────────────────────┘           │
                                                                          ▼
                                                                   RedirectResult
                                                                          │
                                                       ┌──────────────────┼──────────────────┐
                                                       ▼                                       ▼
                                              database.py (SQLite)                    Rich progress bar (cli.py)
                                        scan / results / chain / headers
```

`engine.py` owns *when* to fetch and *whether* to continue the chain;
`analyzer.py` owns *what a single response means*; `detector.py` +
`plugins/` own *how a redirect is recognized* in that response. Splitting
these three concerns is what makes each one independently unit-testable —
`analyzer.py`'s tests never touch a socket, and `engine.py`'s tests mock
HTTP without needing to know anything about meta-refresh parsing.

## The plugin pipeline

`detector.py` runs three redirect-type plugins in a fixed priority order,
returning the first match:

1. **`http_location`** — 301/302/303/307/308 + `Location` header. Checked
   first because this is what real HTTP clients actually follow; a page
   can have an unrelated meta-refresh/JS snippet aimed at older browsers
   that its Location header should take precedence over.
2. **`meta_refresh`** — `<meta http-equiv="refresh">`. Only applies to GET
   responses with a body.
3. **`javascript`** — static pattern matching on `window.location` /
   `location.href` / `.assign()` / `.replace()` in inline `<script>` tags.
   This is a pattern matcher, not a JS engine — dynamic destinations
   (`location.href = someVariable`) are correctly reported as "no redirect
   found" rather than guessed at.

A fourth module, **`cloudflare`**, is deliberately *not* part of this
priority chain. Cloudflare protection is orthogonal to redirect type — a
Cloudflare-fronted origin can 301, meta-refresh, JS-redirect, or return a
plain 200 — so it's classified independently via
`RedirectDetector.classify_cloudflare()` and attached to every result
regardless of what (if any) redirect was found.

Every plugin receives a shared `DetectionContext` per response, which
lazily parses the HTML body with `selectolax` **at most once** and caches
the tree — `meta_refresh` and `javascript` both need the DOM, but neither
pays for a second parse.

## Redirect-chain semantics

This is the single most consequential design decision in the codebase, so
it's documented in three places (here, `analyzer.py`'s module docstring,
and the pytest suite) to prevent silent drift:

- `redirect_chain` contains **only the redirects actually followed** — not
  the terminal (non-redirect) response. `hop_count` is therefore the
  number of redirects traversed: `0` for a direct 200, matching how most
  redirect-auditing tools report it.
- The top-level `status_code` / `redirect_type` / `location` fields always
  describe the **first** response (`redirect_chain[0]` if any redirects
  were followed, otherwise the terminal response itself) — this answers
  "does this candidate URL redirect, and to what?"
- `final_url` / `server` / `content_type` / `cookies` / `fingerprint`
  always describe the **terminal** response — the page actually landed
  on, which is what matters for verifying the destination is the
  intended/authorized target.

## Concurrency model

`engine.py` uses a producer/consumer pattern over a **bounded**
`asyncio.Queue` (`maxsize = workers * 4`), not a plain list of tasks:

- One producer coroutine reads `CandidateURL`s from the (lazy) loader
  generator and pushes them onto the queue.
- `config.workers` consumer coroutines drain the queue concurrently,
  sharing a **single** `httpx.AsyncClient` (one connection pool) rather
  than one client per worker.

This combination is what keeps memory bounded at 100k+ URLs: the input
file is never materialized as a full list of candidates, and the
in-flight working set is capped at a small multiple of the worker count
regardless of how large the input file is.

A single misbehaving candidate (missing `{TARGET}`, malformed URL, an
unexpected exception deep in analysis) is caught per-candidate in
`Engine._process_candidate` and turned into an error result — it never
propagates up and aborts the batch.

## Persistence

`database.py` uses **one shared `aiosqlite` connection**, guarded by an
`asyncio.Lock` for writes, rather than one connection per worker. SQLite
serializes writers internally regardless of how many coroutines attempt to
write concurrently, so sharing one connection under a lock costs nothing
relative to network I/O (a HTTP request takes orders of magnitude longer
than an indexed insert) while avoiding "database is locked" errors that
plague naive multi-connection SQLite access under concurrent writers.

Reads (`iter_results`, used by `export` and `show`) stream results in
bounded batches (default 500 rows) rather than loading an entire scan into
memory — each batch fetches its redirect-chain hops via a single `IN
(...)` query, avoiding N+1 query overhead while still bounding memory.

See [`DATABASE_SCHEMA.md`](./DATABASE_SCHEMA.md) for the full table
reference.

## Dependency injection

Every module that has a meaningful alternate implementation accepts it via
constructor injection rather than importing a concrete dependency
directly:

- `RedirectDetector(plugins=[...])` — swap or reorder the detection
  pipeline.
- `ResponseAnalyzer(detector=...)` — inject a custom detector.
- `Engine(config, analyzer=...)` — inject a custom analyzer (or a fake one
  in tests, with no network involved).
- `Exporter(database)` — the only way exporters touch data, keeping all
  raw SQL inside `database.py`.

This is what makes `tests/test_detector.py::test_dependency_injection_custom_pipeline`
possible: a `RedirectDetector` built with only `HttpLocationPlugin` is
verified to be genuinely blind to JavaScript redirects, not just
configured to ignore them.
