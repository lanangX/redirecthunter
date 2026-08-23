# Spec: `bl-chain` — tiered backlink verification + per-row target override

## Problem Statement

`bl-check` answers "does each URL in this file genuinely link to `-d/--domain`?"
-- exactly one target domain per run, by explicit design decision (see
`.scratch/backlink-check-cli/spec.md`'s "Out of Scope: Multi-domain-per-run").
That is the right shape for auditing "do these pages link to my money site,"
but it cannot express a tiered/pyramid backlink structure, where:

- `backlink1.txt` (tier 1) is meant to link to the money site (e.g. `medilana.id`).
- `backlink2.txt` (tier 2) is meant to link to URLs *in* `backlink1.txt` --
  which is not one domain, it's however many different hosts tier 1 happens
  to use (a real tier-1 list mixes `blogspot.com`, `netlify.app`,
  `notion.site`, `linkedin.com`, `github.com`, short-link redirectors, etc.
  in one file -- see `examples/backlink.txt`).
- `backlink3.txt` (tier 3) is meant to link to URLs in `backlink2.txt`, and so on.

Today, verifying that structure means running `bl-check` once per tier by
hand and finding some way to pass "the URLs in the previous file" as
`-d`, which the flag cannot express at all (it takes one domain string).
There's also no way to pin an individual line in a mixed backlink list to
a specific, different target without splitting it into a whole separate
file -- every line in a `bl-check` input implicitly shares the one
`-d` target for the whole run.

## Solution

Two additive, independently useful changes:

**(A) Per-row target override in backlink input files.** A line in a
`bl-check`/`bl-chain` input file may carry its own explicit target after
a `|` delimiter -- `source_url|target` -- reusing the same delimiter
convention `-H`'s scoped-header syntax already established
(`"linkedin.com|Cookie: li_at=..."`), rather than inventing a new one.
A row without `|target` keeps behaving exactly as it does today. This is
useful on its own, with plain `bl-check`, for an operator auditing a
mixed list where most links target the money site but a few are pinned
to a specific article URL.

**(B) New `bl-chain` command.** Takes an ordered list of tier files as
positional arguments and cascades the target automatically: tier 1 is
checked against `-d/--domain` (the root target -- unchanged, still one
domain, same as `bl-check`); tier *N* (N > 1) is, by default, checked
against the set of hostnames extracted from tier *N-1*'s own input URLs
(overridable per row by (A)). This requires generalizing the matching
core in `redirecthunter/backlink.py` from "one target domain" to "one
target domain, or a set of them" -- `bl-check` itself keeps passing a
one-element set and its documented single-domain-per-run behavior does
not change. Each tier still runs through the existing `bl-check` engine
machinery and is still persisted as an ordinary `backlink_checks` row
(so `bl-stats`/`bl-show`/`bl-export` keep working on any individual tier
exactly as before); `bl-chain` additionally records the ordered
relationship between those tiers so tier-by-tier survivorship can be read
back as one report instead of manually cross-referencing several
`backlink_id`s.

## User Stories

1. As an operator, I want to write `https://blog.example.com/post|medilana.id`
   in a backlink file, so that this one row is checked against a specific
   target even when the rest of the file (or the run's `-d`) targets
   something else.
2. As an operator, I want rows without a `|target` override to keep working
   exactly as they do today, so that every existing backlink file I already
   have keeps working unmodified.
3. As an operator, I want `redirecthunter bl-chain tier1.txt tier2.txt tier3.txt -d medilana.id`,
   so that tier 1 is checked against `medilana.id`, tier 2 is checked
   against whatever hosts tier 1 actually contains, and tier 3 against
   whatever hosts tier 2 contains -- without me manually deriving and
   retyping a target list between runs.
4. As an operator, I want tier order to come from the order I typed the
   files on the command line, not from filename pattern-matching, so that
   `backlink10.txt` sorting before `backlink2.txt` (or any other naming
   scheme I use) never silently reorders my tiers.
5. As an operator, I want each tier to still be an ordinary `bl-check` run
   under the hood (own `backlink_id`, own rows in `backlink_results`), so
   that `bl-stats`/`bl-show`/`bl-export` on a single tier work exactly like
   they do for any other `bl-check` run, with no special-casing to learn.
6. As an operator, I want `redirecthunter bl-chain-stats <chain_id>`, so that
   I get one report showing each tier's confirmed/not-found/blocked counts
   in tier order, not four separate `bl-stats` calls I have to line up myself.
7. As an operator, I want `redirecthunter bl-chain-show <chain_id> --tier 2`,
   so that I can inspect one tier's per-URL results the same way
   `bl-show` already lets me inspect a single `bl-check` run.
8. As an operator, I want `redirecthunter bl-chain-export <chain_id> -f csv`,
   so that I can hand the whole chain's results (with a `tier_index` column)
   to someone who doesn't use the CLI.
9. As an operator, I want short-id prefix resolution to work for `chain_id`
   the same way it already does for `scan_id`/`crawl_id`/`backlink_id`, so
   that I can type an unambiguous prefix instead of a full UUID.
10. As a maintainer, I want the multi-target matching core added to
    `redirecthunter/backlink.py` in a way that is a strict superset of
    today's single-domain behavior, so that `bl-check`'s existing,
    already-documented one-domain-per-run contract does not change and its
    existing tests keep passing unmodified.
11. As a maintainer, I want tier-to-tier target derivation to be an
    explicit, named decision (not an implicit side effect buried in a loop),
    so that a future reader of `bl-chain`'s code can tell at a glance
    whether tier N+1 is checked against *all* of tier N's input URLs or only
    the ones tier N itself confirmed.

## Implementation Decisions

- **Multi-target matching in `redirecthunter/backlink.py`.** `check_one`/
  `check_one_browser` change their `target_domain: str` parameter to
  `target_domains: frozenset[str]` (pre-normalized by the caller via
  `normalize_domain`). `hostname_matches` gains a sibling,
  `hostname_matches_any(hostname, target_domains, *, allow_subdomains)`,
  used internally wherever `hostname_matches` is called today; the
  single-argument `hostname_matches` is kept as-is (still used directly by
  `resolve_domain_headers`, which is genuinely single-domain-scoped and
  unrelated to this change). `bl-check` passes a one-element
  `frozenset({domain})`, so its behavior is provably unchanged -- this is
  the regression guard, not just a description.
- **`matched_target` field.** `BacklinkResult` gains
  `matched_target: str | None` -- which member of `target_domains` a match
  actually matched. Always equal to the (single) target domain in
  `bl-check` mode; meaningful once `bl-chain` passes a real multi-member
  set. Added as a new nullable column on `backlink_results`, via the same
  additive `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` migration path
  `robots_meta`/`robots_header` already used -- no manual migration step.
- **`build_text_mention_pattern` / indirect-query fallback** likewise need
  an any-of-N-domains form (`build_text_mention_pattern_any`) for the same
  reason -- both the anchor pass and the weaker indirect/text-mention
  fallbacks must check against the whole target set, not just one string.
- **Per-row target override, loader side.** `_iter_txt` splits each
  (already comment/blank-filtered) line on the *first* unescaped `|`:
  left-hand side is `raw_url`, right-hand side (if present, stripped) is
  stored as `row_metadata["target"]`. Splitting on the first `|` (not
  last) matches how `-H`'s `domain.com|Name: Value` syntax already reads
  its own scoping prefix, for the same reason: a raw, un-percent-encoded
  `|` is not a legal character inside a URL per RFC 3986, so there is no
  realistic ambiguity with a URL that happens to contain one. `_iter_csv`
  recognizes an optional column literally named `target` (case-insensitive,
  same lookup style already used for the URL column) and, when present,
  pulls it out of `row_metadata` into the same reserved key instead of
  leaving it as generic per-row metadata; if absent, existing behavior is
  untouched. `_iter_json` recognizes an optional `"target"` key on each
  object entry the same way. This reserved `row_metadata["target"]` key is
  read *only* by `bl-check`/`bl-chain`'s call sites -- `scan`/`crawl` never
  look at it, so nothing changes for those commands even on an input file
  that happens to contain the key.
- **Where the per-row override is consumed.** `bl-check`'s and `bl-chain`'s
  execution paths build each URL's effective target set as: the row's own
  `row_metadata["target"]` (normalized, wrapped as a one-element set) if
  present, otherwise the run's (or, in `bl-chain`, the tier's) default
  target set. This resolution happens once per URL before dispatch to
  `check_one`/`check_one_browser`, not inside those functions -- they only
  ever see a plain `frozenset[str]`, keeping the override concept entirely
  out of the matching core.
- **Tier-to-tier target derivation (`bl-chain`).** After tier *N* finishes
  and its results are persisted, tier *N+1*'s default target set is built
  from **all of tier *N*'s input URLs** (every `CandidateURL.raw_url` the
  loader produced for tier *N*'s file), normalized to hostnames -- not
  filtered down to only the rows tier *N* itself confirmed. Rationale:
  whether tier *N* currently, successfully proves its own link to the
  target above it is a separate question from whether tier *N+1* points at
  the URLs the operator *intended* as tier *N*'s inventory; collapsing the
  two would hide "tier N+1 correctly targets tier N, but tier N is
  currently broken/blocked" behind an opaque "tier N+1: not found." A
  `--require-confirmed-parent` flag is available to opt into the stricter
  behavior (derive tier *N+1*'s default target set only from tier *N* rows
  where `match_found` was true) for operators who want that stricter
  question instead; default is off (all input URLs).
- **New model `BacklinkChainConfig`** (`models.py`, alongside
  `BacklinkCheckConfig`): `chain_id`, `domain` (root/tier-1 target),
  `tier_paths: list[Path]` (in the order given on the command line),
  `require_confirmed_parent: bool`, plus the same shared
  `allow_subdomains`/`check_indirect`/`concurrency`/`timeout`/`user_agent`/
  `browser`/... fields `BacklinkCheckConfig` already has -- v1 applies one
  shared set of these to every tier in the chain (see Out of Scope).
- **New model `BacklinkChainSummary`**: `chain_id`, `domain`, `label`,
  overall `status`, and a `tiers: list[BacklinkCheckSummary]` in tier
  order (reusing the existing per-tier summary shape as-is).
- **New tables.** `backlink_chains` (mirrors `backlink_checks`: `chain_id`
  PK, `domain`, `config_json`, `status`, `started_at`, `finished_at`,
  `label`). `backlink_chain_tiers` (`chain_id` FK, `tier_index` INTEGER,
  `backlink_id` FK -> `backlink_checks.backlink_id`, `input_path`) -- a
  thin join/ordering table rather than adding a `chain_id` column directly
  onto `backlink_checks`, so a `backlink_checks` row keeps meaning exactly
  what it means today (a perfectly ordinary, standalone `bl-check` run)
  whether or not it happens to have been created as one tier of a chain.
  `ON DELETE CASCADE` from `backlink_chains` to `backlink_chain_tiers`
  only -- deleting a chain never deletes the underlying `backlink_checks`/
  `backlink_results` rows it pointed at, since those remain independently
  valid, addressable runs.
- **New `Database` methods**, mirroring the `backlink_check*`/`crawl*` set:
  `create_backlink_chain()`, `link_chain_tier(chain_id, tier_index, backlink_id, input_path)`,
  `update_backlink_chain_status()`, `get_backlink_chain_summary()`,
  `list_backlink_chains()`, `resolve_backlink_chain_id()` (short-id prefix
  resolution, same as every other `resolve_*_id`).
- **New CLI commands in `cli.py`:**
  - `bl-chain <tier1> <tier2> [<tier3> ...] -d/--domain ... [--require-confirmed-parent] [-c] [-t] [--exact] [--strict] [-u] [-l] [--database/--db] [--browser] [--headed] [--nav-timeout] [--render-wait] [--header/-H ...] [--headers-file ...]`
    -- flags mirror `bl-check`'s exactly (same short-flag exception
    `AGENT.md` already documents for the `bl-` family; this extends that
    exception rather than creating a third one). Runs each tier in order
    (tier *N+1* cannot start target-resolution until tier *N*'s results are
    persisted), printing one progress bar per tier (same shape
    `_bl_check_progress_description` already produces, reused per tier)
    and a final combined summary, ending with the `chain_id`.
  - `bl-chain-stats [chain_id] [--database/--db]`: mirrors `bl-stats`,
    printing every tier's summary in order for one chain, or (with no
    argument) one row per chain across all chains, mirroring `bl-stats`'s
    no-argument list mode.
  - `bl-chain-show <chain_id> --tier <N> [--confirmed] [--type] [--limit] [--database/--db]`:
    resolves tier *N*'s underlying `backlink_id` via
    `backlink_chain_tiers` and otherwise behaves exactly like `bl-show`
    against that id -- no new per-URL display logic, this is routing, not
    a new table renderer.
  - `bl-chain-export <chain_id> -f/--format csv|json [-o/--output] [--database/--db]`:
    exports every tier's rows concatenated in tier order, with one added
    `tier_index` column prepended to the existing `BACKLINK_RESULT_COLUMNS`.
- **Tier order is always explicit positional arguments** -- `bl-chain`
  never infers order from filenames (`backlink1.txt`, `backlink2.txt`, ...).
  This is a deliberate rejection of filename-pattern auto-discovery: sort
  order over arbitrary user-chosen filenames is exactly the kind of thing
  that silently does the wrong thing (`backlink10.txt` alphabetically
  precedes `backlink2.txt`; nothing enforces the convention exists at all).
  A `--glob "backlink*.txt"` convenience flag (natural-sorted, then still
  displayed back to the operator before running so a bad match order is
  caught before any requests go out) is a plausible v2 addition, not v1.
- **Concurrency/rate-limiting**: unchanged: each tier's run is driven by
  the same `run_backlink_checks`/`run_backlink_checks_browser` machinery
  `bl-check` already uses, just parameterized with a `frozenset` target
  set instead of a single string.

## Testing Decisions

- `tests/test_backlink.py`: table-driven tests for `hostname_matches_any`
  and `build_text_mention_pattern_any` covering the same false-positive
  shapes the existing single-domain tests already cover
  (`notmedilana.id`, `medilana.id.evil.com`, subdomain-allowed vs.
  `--exact`), plus multi-member-set cases (matches second member, matches
  neither, matches two members where only one should win and
  `matched_target` records which). Existing single-domain `check_one`
  tests must keep passing unmodified against the new
  `frozenset`-of-one call shape -- this is the regression guard that (A)/(B)
  didn't change `bl-check`'s own behavior.
- `tests/test_loader.py`: new cases for `_iter_txt`'s `|target` split
  (present, absent, extra whitespace around either side, a line with more
  than one `|` splitting only on the first), `_iter_csv`'s optional
  `target` column (present, absent, case-insensitive header match), and
  the equivalent `_iter_json` `"target"` key.
- New `tests/test_backlink_chain.py`: end-to-end chain tests (2 and 3
  tiers) against local HTTP fixtures, asserting tier 2's *default* target
  set is derived from tier 1's actual input URLs, that a per-row `|target`
  override wins over that derived default, and that
  `--require-confirmed-parent` changes tier *N+1*'s derived target set
  when tier *N* has a mix of confirmed/not-found rows. Plus `Database`
  tests for `backlink_chains`/`backlink_chain_tiers` (create, link tier,
  cascade-delete behavior, short-id prefix resolution).
- `tests/test_cli.py`: new `CliRunner` cases for `bl-chain`,
  `bl-chain-stats`, `bl-chain-show --tier`, `bl-chain-export`, in the same
  style as the existing `bl-check`/`bl-stats`/`bl-show`/`bl-export` cases.

## Out of Scope

- Per-tier distinct `concurrency`/`timeout`/`user-agent`/`allow_subdomains`/
  etc. -- v1 applies one shared set of flags to every tier in a chain, the
  same way `bl-check`'s own flags apply uniformly to its one input file.
- More than one root/final target domain per chain -- `-d` is still
  exactly one domain (tier 1's target); only the *middle* tiers gain
  multi-target matching. This does not reopen `bl-check`'s own
  one-domain-per-run scope decision, which is unchanged.
- Filename-pattern auto-discovery of tier files (`backlinkN.txt` globbing
  with implied ordering). Tier order is always explicit positional
  arguments in v1; a `--glob` convenience flag is a plausible later
  addition, not part of this spec.
- Resume support for an interrupted `bl-chain` run, matching `bl-check`'s
  and `crawl`'s own documented v1 scope decision.
- `bl-chain-export --format sqlite` (CSV/JSON only, matching `bl-export`).
- Any change to `scan`/`crawl`'s TXT/CSV/JSON loading behavior. The
  `|target` / `target` reserved-key extension is read only by
  `bl-check`/`bl-chain`'s own call sites; a `scan`/`crawl` input file
  containing a `|` in a line, or a `target` column/key, is treated
  exactly as before everywhere else in the CLI.
- Cycle detection across tiers (e.g. a tier-3 file that happens to also
  appear, unmodified, as the tier-1 file) -- `bl-chain` runs each given
  file exactly once, in the order given; a operator-constructed cycle is
  not specially detected or rejected in v1.

## Further Notes

- Docs to update once implementation starts: `docs/CLI_REFERENCE.md` (new
  command family + `bl-check`'s own reference gains a short note on the
  `|target` row syntax), `docs/DATABASE_SCHEMA.md` (new tables +
  `matched_target` column), `CHANGELOG.md`, `MEMORY.md`, and `AGENT.md`'s
  short-flag-exception note (extended, not a new bullet, since `bl-chain`
  is the same documented exception as the rest of the `bl-` family).
- New fixtures belong in `examples/` per `AGENT.md`'s "examples/ files are
  verified against the real loader/code, never illustrative-only"
  convention -- e.g. `examples/backlink-tier1.txt`,
  `examples/backlink-tier2.txt`, `examples/backlink-tier3.txt` (small,
  real, loader-valid files demonstrating both a plain row and a
  `|target`-override row), replacing the ambiguous `backlink1.txt`/
  `backlink2.txt`/... naming from the originating conversation with names
  that don't imply filename-order-based tier detection is how the feature
  actually works.
- This spec treats (A) and (B) as one ticket sequence because (B) depends
  on (A)'s loader changes, but they remain separately shippable/testable
  checkpoints -- (A) alone is already useful to `bl-check` users before
  `bl-chain` exists at all, which the "Testing Decisions" section above
  reflects by testing the loader changes independently of the chain
  end-to-end tests.
