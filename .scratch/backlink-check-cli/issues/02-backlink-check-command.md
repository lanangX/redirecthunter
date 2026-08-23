# 02 — `bl-check` command with SQLite persistence

**What to build:** A new `redirecthunter bl-check <input-file> -d/--domain
<domain>` command that checks every URL in the input file for a genuine
outbound link to the domain (reusing `redirecthunter/backlink.py` from
ticket 01), shows a live Rich progress bar while running, and persists the
run's config and every per-URL result to `redirecthunter.db` in two new
tables (`backlink_checks`, `backlink_results`). The command prints the
same kind of end-of-run summary `backlink_checker.py` already prints, plus
the new run's `backlink_id`.

Flags (short `bl-` command family, per the spec's naming/UX decision):
`-c/--concurrency`, `-t/--timeout`, `--exact` (replaces
`--no-subdomains`), `--strict` (replaces `--no-indirect`), `-u/--agent`,
`-l/--label`, `--database/--db`. Input file accepts the same
txt/csv/json/sqlite formats `scan`/`crawl` already accept, via the
existing `load_candidates()` loader.

This is the vertical slice: schema + model + persistence + CLI command,
demoable end-to-end by running the command and confirming a new row exists
in `redirecthunter.db`.

**Blocked by:** 01 — needs `redirecthunter/backlink.py` to exist.

**Status:** done

- [x] `backlink_checks` / `backlink_results` tables added to `database.py`'s schema, indexed on `(backlink_id, match_found)`.
- [x] `BacklinkCheckConfig` / `BacklinkCheckSummary` models added to `models.py`.
- [x] `Database.create_backlink_check()`, `save_backlink_result()`, `update_backlink_check_status()`, `get_backlink_check_summary()` implemented, mirroring the equivalent `crawl*` methods.
- [x] `redirecthunter bl-check <input-file> -d ...` runs to completion, shows progress, persists config + every result, and prints the run's `backlink_id`.
- [x] Concurrency is driven by `engine.py`'s `RateLimiter`/`MAX_BODY_BYTES` over a fixed candidate list (`Engine`'s bounded-queue shape), not a new queue implementation.
- [x] `tests/test_backlink.py` covers the new `Database` methods (style: `tests/test_crawl_database.py`).
- [x] `tests/test_cli.py` covers `bl-check` end-to-end via `CliRunner` against a temp input file + local HTTP fixture.
