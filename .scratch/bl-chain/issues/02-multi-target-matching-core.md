# 02 — Multi-target matching core in `redirecthunter/backlink.py`

**What to build:** Generalize the matching core from "one target domain"
to "one target domain, or a set of them," as a strict superset of today's
behavior:

- `hostname_matches_any(hostname, target_domains: frozenset[str], *, allow_subdomains)`
  — new sibling of `hostname_matches`, used everywhere a single target
  domain was compared before. `hostname_matches` itself is untouched
  (still used by `resolve_domain_headers`, which is genuinely
  single-domain-scoped).
- `build_text_mention_pattern_any(target_domains: frozenset[str])` — new
  sibling of `build_text_mention_pattern`, combining every target into one
  boundary-aware alternation pattern.
- `check_one` / `check_one_browser`: parameter renamed/changed from
  `target_domain: str` to `target_domains: frozenset[str]` (pre-normalized
  by the caller). Every internal call site (`final_url_is_target` check,
  anchor-hostname match, indirect-query fallback, text-mention fallback)
  uses the `_any` helpers against the full set.
- `BacklinkResult` gains `matched_target: str | None` — which member of
  `target_domains` actually matched. Add to `BACKLINK_RESULT_COLUMNS`.
- `run_backlink_checks` / `run_backlink_checks_browser`: parameter renamed
  to `target_domains`, threaded through to `check_one`/`check_one_browser`.
- **Per-row override resolution** happens in the caller (`bl-check`'s /
  `bl-chain`'s execution path), one level above `check_one`: for each URL,
  effective target set = `{normalize_domain(row.row_metadata["target"])}`
  if present, else the run's (or tier's) default `target_domains`. The
  matching functions in `backlink.py` only ever see a plain
  `frozenset[str]` — they don't know about `row_metadata` or overrides at all.
- `bl-check`'s CLI command passes `frozenset({domain})` — one-element set
  — so its own documented one-domain-per-run behavior is provably
  unchanged.

**Blocked by:** None (independent of ticket 01, though `bl-chain` in
ticket 03 needs both).

**Status:** done

- [x] `hostname_matches_any` / `build_text_mention_pattern_any` added; existing single-target helpers untouched.
- [x] `check_one` / `check_one_browser` / `run_backlink_checks` / `run_backlink_checks_browser` take `target_domains: frozenset[str]`.
- [x] `BacklinkResult.matched_target` added; included in `BACKLINK_RESULT_COLUMNS`.
- [x] `tests/test_backlink.py`: multi-member-set cases (matches 2nd of 3, matches none, `matched_target` records the right one) alongside every existing false-positive-guard case, now run against 1-, 2-, and N-member sets.
- [x] Existing single-domain `check_one` tests pass unmodified against the new `frozenset`-of-one call shape (regression guard for `bl-check`).
- [x] `bl-check`'s CLI command updated to build and pass `frozenset({domain})`; its own behavior/output is unchanged.
