# Spec: Add `bl-check` as a first-class, SQLite-persisted RedirectHunter CLI command

## Problem Statement

`backlink_checker.py` (root-level, standalone) already answers the right
question for backlink auditing -- does a page's rendered HTML genuinely
contain an outbound link to a target domain, as opposed to `scan`'s
different question (does a URL *redirect to* a target). But it lives
entirely outside the CLI/database layer `scan` and `crawl` already have:

- Every run is a one-shot CSV dump. There's no `scan_id`/`crawl_id`-style
  run identity, no `stats`/`show`/`export` to revisit a prior run, and no
  way to compare or filter past results without re-running the check.
- It doesn't share RedirectHunter's SQLite persistence
  (`redirecthunter.db`), so backlink-audit history lives in scattered CSV
  files instead of the same place every other scan/crawl's history lives.
- Operators auditing backlinks (e.g. a list of pages claiming to link to
  `medilana.id`) have to context-switch to a different script with a
  different invocation style instead of staying inside `redirecthunter`.

## Solution

Add a new, first-class CLI command family -- `bl-check`, `bl-stats`,
`bl-show`, `bl-export` -- that runs the same matching logic as
`backlink_checker.py` (extracted into a shared, importable module) but
persists every run and its per-URL results to `redirecthunter.db`, exactly
the way `crawl`/`crawl-stats`/`crawl-show`/`crawl-export` already do for
crawl mode.

**Naming/UX decision:** the command family uses a short `bl-` prefix
(`bl-check`/`bl-stats`/`bl-show`/`bl-export`) instead of a full
`backlink-` prefix, and several flags get single-letter aliases
(`-d/--domain`, `-c/--concurrency`, `-t/--timeout`, `-u/--agent`,
`-l/--label`, `-f/--format`, `-o/--output`). This is a deliberate,
requested deviation from `AGENT.md`'s "long `--option` flags only"
convention -- the same kind of documented exception already granted to
`redact-target`/`expand-target`, this time justified by day-to-day typing
ergonomics rather than pre-existing script muscle memory. Two boolean
flags are also reworded shorter and clearer by inverting their phrasing:
`--no-subdomains` becomes `--exact` (match the domain exactly, no
subdomains) and `--no-indirect` becomes `--strict` (skip weaker/indirect
match signals). `docs/CLI_REFERENCE.md` and `AGENT.md`'s convention note
must both reflect this as the second documented exception (ticket 05).

`backlink_checker.py` and `backlink_checker_js.py` stay exactly where they
are, for the same reason `redact-target`/`expand-target` didn't delete the
shell-script workflow they replaced: they're a legitimate quick, no-database
path for a one-off CSV check. Both scripts are refactored to import their
matching logic from the new shared module instead of owning it, so there is
still exactly one implementation of the false-positive-guarding hostname
match -- never two copies that can drift.

## User Stories

1. As a RedirectHunter operator, I want to run `redirecthunter bl-check <input-file> -d medilana.id`, so that every URL in the file is checked for a genuine outbound link to my domain and the run is saved for later.
2. As an operator, I want `bl-check` to accept the same input formats `scan` does (txt/csv/json/sqlite, via the existing loader), so that I don't have to reformat a backlink list I already prepared for another command.
3. As an operator, I want `bl-check` to print a live progress bar (worker count, checked/broken counts, ETA) while it runs, so that I have the same visibility `scan`/`crawl` already give me on large lists.
4. As an operator, I want short flags -- `-d/--domain`, `-c/--concurrency`, `-t/--timeout`, `-u/--agent`, `-l/--label`, plus `--exact` and `--strict` -- so that typing a `bl-check` invocation is fast and doesn't feel like the long-flag-only rest of the CLI.
5. As an operator, I want every run's config (`domain`, flag values, input file) and per-URL result (`match_found`, `match_type`, `matched_href`, `rel`, `target`, `blocked`, `requires_login`, `text_mentions`, `notes`, `status_code`, `final_url`, `error`) persisted to `redirecthunter.db`, so that I can revisit results without re-running the check.
6. As an operator, I want a short `backlink_id` printed at the end of the run (mirroring `scan_id`/`crawl_id`), so that I can reference this specific run in later commands.
7. As an operator, I want `redirecthunter bl-stats` with no argument to list every past `bl-check` run (id, domain, label, status, confirmed/not-found/blocked counts), so that I can find a prior run without remembering its id.
8. As an operator, I want `redirecthunter bl-stats <backlink_id>` to show the aggregate summary for one run (counts per `match_type`, confirmed vs. not-found vs. blocked vs. requires-login vs. error), so that I get the same kind of report `print_summary()` already gives, but pulled from the database.
9. As an operator, I want `redirecthunter bl-show <backlink_id>` to display individual per-URL results in a Rich table (source URL, status, match found, match type, matched href, rel), so that I can inspect exactly which URLs confirmed a backlink and which didn't.
10. As an operator, I want a `--confirmed` filter on `bl-show` (mirroring `crawl-show --broken-only`), so that I can jump straight to the URLs that genuinely link back to me.
11. As an operator, I want a `--type` filter on `bl-show` accepting any of the existing match-type values, so that I can isolate weaker signals (e.g. `text_mention_only`, `indirect_query`) for manual review.
12. As an operator, I want `redirecthunter bl-export <backlink_id> -f csv` (and `-f json`) to write the full per-URL result set to a file, so that I can hand a report to someone who doesn't use the CLI.
13. As an operator, I want short-id prefix resolution (like `scan`/`crawl` already have) to work for `backlink_id` in every one of these commands, so that I can type an unambiguous prefix instead of a full UUID.
14. As a maintainer, I want `BacklinkResult`, `check_one()`, and every matching/detection helper (`hostname_matches`, `normalize_domain`, `normalize_hostname`, `extract_hostname`, `looks_like_login_wall`, `looks_like_bot_block_status`, `looks_like_challenge_page`, `build_text_mention_pattern`) moved into a new `redirecthunter/backlink.py` module, so that the CLI command and both root scripts share one implementation.
15. As a maintainer, I want `backlink_checker.py` and `backlink_checker_js.py` refactored to import from `redirecthunter/backlink.py` instead of defining this logic themselves, with their existing CLI behavior and output completely unchanged, so that nothing about the standalone-script workflow regresses.
16. As a maintainer, I want new tables `backlink_checks` (one row per run: id, domain, config, status, timestamps, label) and `backlink_results` (one row per URL checked, FK to `backlink_checks`), following the same shape as `crawls`/`crawl_pages`, so that the schema stays consistent with how the rest of the database models a "run + its many results."
17. As a maintainer, I want `backlink-check` to reuse `engine.py`'s `RateLimiter` and body-size cap the same way `crawl` does, rather than reimplementing concurrency control, so that the concurrency behavior is consistent across every command that fetches URLs.
18. As a maintainer, I want `backlink-check` to run one domain per invocation (one `backlink_id` = one domain, one input file), matching how one `crawl_id` is one seed, so that the schema and CLI stay simple; multi-domain-per-run is explicitly deferred.
19. As a maintainer, I want `backlink-check` to skip resumability in v1, matching `crawl`'s own documented v1 scope decision, so that this feature doesn't grow scope beyond what's being asked for.
20. As a maintainer, I want `backlink-export` to support CSV and JSON only in v1 (no `--format sqlite`), matching `crawl-export`'s own documented v1 scope decision, for the same reason.
21. As a maintainer, I want new tests in `tests/test_backlink.py` (matching-logic + database tests) and additions to `tests/test_cli.py` (command tests), so that coverage lives where the project's existing test-file convention expects it.
22. As a maintainer, I want `docs/CLI_REFERENCE.md`, `docs/DATABASE_SCHEMA.md`, `CHANGELOG.md`, and `MEMORY.md` updated to reflect the new command family and tables, so that the project's own documentation map (`AGENT.md`) stays accurate.

## Implementation Decisions

- **New module `redirecthunter/backlink.py`**: owns `BacklinkResult` (as a Pydantic model rather than the script's current `@dataclass`, so it can flow through the same database-serialization path as `CrawlPageResult`/`CrawlLinkResult`), `check_one()` (async, given an `httpx.AsyncClient`), and every hostname/pattern-matching helper currently in `backlink_checker.py`. `backlink_checker.py` keeps its own `argparse` CLI, CSV writer, and Rich summary table, but imports the model/logic from this module instead of defining it. `backlink_checker_js.py`'s existing import line is repointed at the new module (it already imports this logic from `backlink_checker.py`, so this is a one-line change there).
- **New Pydantic model `BacklinkCheckConfig`** (in `models.py`, alongside `CrawlConfig`): `backlink_id`, `domain`, `input_path`, `input_format`, `allow_subdomains`, `check_indirect`, `concurrency`, `timeout`, `user_agent`, `database_path`, `label`.
- **New Pydantic model `BacklinkCheckSummary`** (alongside `CrawlSummary`): aggregate counts by `match_type`, plus `confirmed`/`blocked`/`requires_login`/`error` totals, `total_urls`, `status`, timestamps.
- **New tables** `backlink_checks` (mirrors `crawls`: `backlink_id` PK, `domain`, config columns, `status`, `started_at`, `finished_at`, `label`) and `backlink_results` (mirrors `crawl_pages`/`crawl_links`: `result_id` PK, `backlink_id` FK, `source_url`, `final_url`, `status_code`, `match_found`, `match_type`, `matched_href`, `rel`, `target`, `blocked`, `requires_login`, `text_mentions`, `notes` (joined string), `error`, `checked_at`), each with the same kind of index `crawl_links` has on `(crawl_id, is_broken)` -- here `(backlink_id, match_found)`.
- **New `Database` methods**, mirroring the `crawl*` set exactly: `create_backlink_check()`, `save_backlink_result()`, `update_backlink_check_status()`, `get_backlink_check_summary()`, `list_backlink_checks()`, `resolve_backlink_check_id()`, `iter_backlink_results(backlink_id, *, confirmed_only=False, match_type=None)`. The last one is consumed the same guarded way `crawl-show`'s fix in this session established: always wrapped in `contextlib.aclosing(...)` at every call site, never a bare `async for` that can `break` early.
- **New CLI commands in `cli.py`**, following `crawl`'s four-command shape, with a short `bl-` prefix and single-letter flag aliases as the second documented exception to `AGENT.md`'s long-flags-only convention:
  - `bl-check <input-file> -d/--domain ... [-c/--concurrency] [-t/--timeout] [--exact] [--strict] [-u/--agent] [-l/--label] [--database/--db]`: loads candidates via the existing `load_candidates()` path (same as `crawl --input-file` does today), runs the check pool with a Rich progress bar, persists config + every result, prints the same kind of summary `backlink_checker.py --domain` already prints, ends by printing the `backlink_id`. `--exact` inverts and replaces `--no-subdomains` (match the domain exactly, no subdomains); `--strict` inverts and replaces `--no-indirect` (skip weaker/indirect match signals).
  - `bl-stats [backlink_id] [--database/--db]`: mirrors `stats`.
  - `bl-show <backlink_id> [--confirmed] [--type] [--limit] [--database/--db]`: mirrors `crawl-show --type links`.
  - `bl-export <backlink_id> -f/--format csv|json [-o/--output] [--database/--db]`: mirrors `crawl-export`.
- **Concurrency**: `backlink-check` reuses `engine.py`'s `RateLimiter` and `MAX_BODY_BYTES` constant, run via a bounded worker pool over the fixed candidate list (this is a fixed-frontier problem like `scan`, not a discovered-frontier problem like `crawl` -- no new queue-termination strategy is needed; `Engine`'s existing bounded-queue-plus-sentinel shape is the right fit, not `Crawler`'s unbounded queue).
- **Scope boundary respected from prior decisions**: one domain per run, no resume, CSV/JSON export only, httpx-only (no Playwright/JS variant in this command).

## Testing Decisions

- New file `tests/test_backlink.py`: table-driven tests for every matching helper moved into `redirecthunter/backlink.py` (ported from whatever `tests/test_backlink_checker.py` already covers, since the logic itself isn't changing -- just its location), plus `Database` tests for the new `backlink_checks`/`backlink_results` methods, in the same style as `tests/test_crawl_database.py`.
- `tests/test_backlink_checker.py` (the existing root-script test file) must keep passing unmodified after the extraction -- it's the regression guard proving `backlink_checker.py`'s behavior didn't change when its logic moved.
- `tests/test_cli.py` gets new cases for all four commands using Typer's `CliRunner`, in the same style as the existing `crawl`/`crawl-stats`/`crawl-show`/`crawl-export` tests: run against a temp input file + a mocked/local HTTP fixture, assert on exit code, printed summary content, and (for `-show`/`-export`) the persisted row shape.
- Good tests here assert on observable behavior (DB row contents, printed table contents, exported file contents) -- not on internal SQL string construction.

## Out of Scope

- Multi-domain-per-run.
- Resume support for `backlink-check`.
- `backlink-export --format sqlite`.
- A Playwright/headless variant of `backlink-check` (stays `backlink_checker_js.py`-only for now).
- Any change to `backlink_checker.py`'s or `backlink_checker_js.py`'s CLI flags, output format, or CSV shape -- their behavior is preserved exactly; only where their logic lives changes.
- Deleting or deprecating either root script.

## Further Notes

- The full grilling/decision history behind this spec is in the parent conversation; this file is the synthesized result, not a transcript.
- This spec directly addresses (rather than routes around) the guardrail already written in `MEMORY.md` under "Why `backlink_checker.py` / `backlink_checker_js.py` live at the repo root instead of inside `redirecthunter/`" -- that note explicitly says SQLite persistence for backlink verification is "a real feature worth its own spec." This is that spec.
