# 02 — `_build_per_url_targets` builds a multi-member `frozenset`

**What to build:** Update `_build_per_url_targets` (`cli.py`) to read
`row_metadata["target"]` as a `tuple[str, ...]` (per ticket 01) and
build `frozenset(normalize_domain(t) for t in targets)` for every
candidate that has one, instead of always wrapping a single string.

- A one-element tuple must produce the exact same one-element
  `frozenset` `_build_per_url_targets` already produces today for a
  bare-string override -- this is the backward-compatibility regression
  guard for every existing `bl-check`/`bl-chain` input file.
- No changes needed to `run_backlink_checks`/`run_backlink_checks_browser`
  or anything in `backlink.py` -- both already accept an
  arbitrary-size `frozenset[str]` per URL via `per_url_targets` (added by
  `bl-chain`); this ticket only changes what gets built on the calling
  side.
- `matched_target` on `BacklinkResult` already exists and already
  records whichever set member matched -- no schema change here.

**Blocked by:** Ticket 01 (needs `row_metadata["target"]` as a tuple).

**Status:** done

- [x] `_build_per_url_targets` normalizes every entry in a multi-target
      tuple into one `frozenset`.
- [x] Single-entry tuple produces the identical one-element `frozenset`
      as today's bare-string path (regression guard).
- [x] Unit test: multi-entry override produces the expected
      multi-member `frozenset`; single-entry produces the same result
      as before the loader type change.
- [x] `tests/test_backlink.py` / `test_backlink_chain.py`: end-to-end
      row with `|target1;target2` matches on the second target and
      `matched_target` records that specific one.
