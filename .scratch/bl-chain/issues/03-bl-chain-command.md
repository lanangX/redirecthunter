# 03 — `bl-chain` command, models, and database tables

**What to build:**

- **Models** (`models.py`): `BacklinkChainConfig` (`chain_id`, `domain`,
  `tier_paths: list[Path]`, `require_confirmed_parent: bool`, plus the
  shared `allow_subdomains`/`check_indirect`/`concurrency`/`timeout`/
  `user_agent`/`browser`/`headed`/`nav_timeout`/`render_wait`/
  `block_resources`/`database_path`/`label` fields `BacklinkCheckConfig`
  already has) and `BacklinkChainSummary` (`chain_id`, `domain`, `label`,
  overall `status`, `tiers: list[BacklinkCheckSummary]` in tier order).
- **Database tables**: `backlink_chains` (mirrors `backlink_checks`) and
  `backlink_chain_tiers` (`chain_id` FK, `tier_index`, `backlink_id` FK ->
  `backlink_checks.backlink_id`, `input_path`), `ON DELETE CASCADE` from
  `backlink_chains` to `backlink_chain_tiers` only — a chain delete never
  touches the underlying `backlink_checks`/`backlink_results` rows.
- **New `Database` methods**: `create_backlink_chain()`,
  `link_chain_tier(chain_id, tier_index, backlink_id, input_path)`,
  `update_backlink_chain_status()`, `get_backlink_chain_summary()`,
  `list_backlink_chains()`, `resolve_backlink_chain_id()` (short-id prefix
  resolution matching every other `resolve_*_id`).
- **CLI command `bl-chain <tier1> <tier2> [<tier3> ...] -d/--domain ...`**
  mirroring `bl-check`'s flags exactly (`-c`, `-t`, `--exact`, `--strict`,
  `-u`, `-l`, `--database/--db`, `--browser`, `--headed`, `--nav-timeout`,
  `--render-wait`, `-H`/`--header`, `--headers-file`), plus
  `--require-confirmed-parent`. Runs tiers strictly in order: tier 1 vs
  `frozenset({domain})`; tier N>1 vs the normalized-hostname set built
  from tier N-1's own input URLs (all of them by default, or only
  `match_found` rows when `--require-confirmed-parent` is set), with each
  row's own `row_metadata["target"]` override (ticket 01) still winning
  per-row within any tier. Each tier is persisted as an ordinary
  `backlink_checks`/`backlink_results` run (reusing `run_backlink_checks`/
  `run_backlink_checks_browser` from ticket 02 unmodified) and linked via
  `link_chain_tier`. Prints one progress bar per tier, then a combined
  summary, ending with the `chain_id`.

**Blocked by:** 01, 02.

**Status:** done

- [x] `BacklinkChainConfig` / `BacklinkChainSummary` added to `models.py`.
- [x] `backlink_chains` / `backlink_chain_tiers` tables added to `database.py`'s schema, with the documented cascade behavior.
- [x] New `Database` methods listed above implemented and covered by `tests/test_database.py`-style tests.
- [x] `bl-chain` command implemented; tier order is always the given positional arguments, never filename-pattern-inferred.
- [x] Tier N>1's default target set is built from tier N-1's own input URLs (all, or confirmed-only under `--require-confirmed-parent`); per-row override still wins.
- [x] `tests/test_backlink_chain.py`: 2- and 3-tier end-to-end tests against local HTTP fixtures, covering both `--require-confirmed-parent` states and a per-row override beating the derived default.
- [x] `tests/test_cli.py`: `bl-chain` `CliRunner` case(s).

**Verified 2026-08-23 (session that actually implemented this ticket):**
all seven boxes above independently re-checked against real code, not
just re-ticked from a prior claim -- see `MEMORY.md`'s in-flight entry
for the full verification trail (this ticket's own checkboxes were false
before this session; they're accurate now). Full regression at the time
of this edit: `pytest` 369 passed, `ruff check .` clean, `mypy
redirecthunter` clean.
