# 03 — `bl-stats` and `bl-show` read commands

**What to build:** Two read-only commands over a persisted `bl-check` run:

- `redirecthunter bl-stats [backlink_id]` — with no argument, lists
  every past run (id, domain, label, status, confirmed/not-found/blocked
  counts), mirroring `stats`. With a `backlink_id` (full or short prefix),
  shows that run's aggregate summary (counts per `match_type`, plus
  confirmed/blocked/requires-login/error totals).
- `redirecthunter bl-show <backlink_id> [--confirmed] [--type ...]
  [--limit]` — displays individual per-URL results in a Rich table
  (source URL, status, match found, match type, matched href, rel),
  mirroring `crawl-show --type links`.

Both resolve short `backlink_id` prefixes the same way `resolve_scan_id`/
`resolve_crawl_id` already do.

**Blocked by:** 02 — needs persisted runs/results to read.

**Status:** done

- [x] `Database.list_backlink_checks()` and `resolve_backlink_check_id()` implemented, mirroring `list_scans()`/`resolve_scan_id()`.
- [x] `Database.iter_backlink_results(backlink_id, *, confirmed_only=False, match_type=None)` implemented; every call site wraps it in `contextlib.aclosing(...)` (per the `crawl-show` fix already landed this session — no bare `async for` that can `break` early).
- [x] `bl-stats` (list and single-run modes) prints a Rich table/summary matching the project's existing `stats` output style.
- [x] `bl-show` supports `--confirmed`, `--type`, and `--limit`, and resolves short-id prefixes.
- [x] `tests/test_cli.py` covers both commands against a run persisted by ticket 02's command.
