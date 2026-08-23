# 04 — `bl-export` command

**What to build:** `redirecthunter bl-export <backlink_id> -f/--format
csv|json [-o/--output]`, writing the full per-URL result set for one run
to a file, mirroring `crawl-export`'s streaming (memory-bounded) export
shape. No `--format sqlite` in this ticket (matches `crawl-export`'s own
documented v1 scope).

**Blocked by:** 02 — needs persisted results to export.

**Status:** done

- [x] `bl-export <backlink_id> -f csv` writes a CSV with one row per result, columns matching `backlink_results`.
- [x] `bl-export <backlink_id> -f json` writes the equivalent as JSON.
- [x] Export streams from `iter_backlink_results()` (wrapped in `contextlib.aclosing(...)`) rather than buffering the full result set in memory, matching `crawl-export`'s existing approach.
- [x] `tests/test_cli.py` / `tests/test_export.py` cover both formats against a run persisted by ticket 02's command.
