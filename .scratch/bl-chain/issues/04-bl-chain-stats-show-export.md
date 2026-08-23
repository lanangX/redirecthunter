# 04 — `bl-chain-stats` / `bl-chain-show` / `bl-chain-export`

**What to build:**

- `bl-chain-stats [chain_id] [--database/--db]`: with an id, prints every
  tier's `BacklinkCheckSummary` in tier order for that chain (reusing the
  existing `bl-stats` per-run summary rendering); with no id, lists every
  chain (mirroring `bl-stats`'s no-argument list mode).
- `bl-chain-show <chain_id> --tier <N> [--confirmed] [--type] [--limit] [--database/--db]`:
  resolves tier N's underlying `backlink_id` via `backlink_chain_tiers`
  and delegates to the exact same rendering `bl-show` already uses against
  that id — no new per-URL table logic, this is routing only.
- `bl-chain-export <chain_id> -f/--format csv|json [-o/--output] [--database/--db]`:
  exports every tier's rows concatenated in tier order, with a `tier_index`
  column prepended to the existing `BACKLINK_RESULT_COLUMNS` (which now
  also includes `matched_target` from ticket 02).

**Blocked by:** 03.

**Status:** done

- [x] `bl-chain-stats` (both with and without an id) implemented.
- [x] `bl-chain-show --tier` implemented, delegating to `bl-show`'s existing rendering.
- [x] `bl-chain-export` implemented for CSV and JSON, with a `tier_index` column; no `--format sqlite` (matches `bl-export`'s own v1 scope).
- [x] Short-id prefix resolution works for `chain_id` in all three commands.
- [x] `tests/test_cli.py`: `CliRunner` cases for all three commands.
