# MEMORY.md

Living memory for agents picking up work on this repo across sessions.
Not a changelog (see `CHANGELOG.md` for released behavior) and not
architecture reference (see `docs/ARCHITECTURE.md` for the stable "how it
works") — this is *current state*: what's in flight, what was decided and
why, what to check before starting something new. Update this file
whenever you finish or hand off a piece of work; prune entries once
they're fully landed and reflected in `CHANGELOG.md`/`docs/`.

## In-flight work

**Implemented (2026-08-23 third session) — `bl-chain` ticket 03
(models, database tables/methods, CLI command, tests). Ticket 03's own
file-level `**Status:** done` was false, exactly as the prior session's
hand-off flagged** -- verified directly against the code before starting
(`grep -rn "BacklinkChain\|bl-chain\|backlink_chain"
redirecthunter/*.py`): the only pre-existing hits were forward-looking
docstring/comment references (`backlink.py:100,304,515`,
`cli.py:2194`, `loader.py:81,169`) describing `bl-chain` as a *future*
consumer of ticket 01/02's infrastructure -- no `BacklinkChainConfig`,
no `backlink_chains` table, no `bl-chain` command, no
`tests/test_backlink_chain.py` existed anywhere in the tree. Ticket 01
(`|target` per-row override) and 02 (multi-target matching core,
`matched_target`) were, as the hand-off said, genuinely done and already
in use by `bl-check`.

What was actually built this session (ticket 03 scope only -- 04/05
`bl-chain-stats`/`bl-chain-show`/`bl-chain-export`/docs-sync remain
**unimplemented and unverified**, see below):

- `models.py`: `BacklinkChainConfig` (chain_id, domain, tier_paths,
  require_confirmed_parent, plus the shared fields `BacklinkCheckConfig`
  already has) and `BacklinkChainSummary` (chain_id, domain, label,
  status, `tiers: list[BacklinkCheckSummary]`), both in `__all__`.
- `database.py`: `backlink_chains` (mirrors `backlink_checks`) and
  `backlink_chain_tiers` (join/ordering table -- `chain_id` FK,
  `tier_index`, `backlink_id` FK -> `backlink_checks.backlink_id`,
  `input_path`) added to `_SCHEMA_SQL`. `ON DELETE CASCADE` only from
  `backlink_chains` to `backlink_chain_tiers` -- deleting a chain never
  touches the underlying `backlink_checks`/`backlink_results` rows
  (verified by a dedicated test, see below). **Design decision**: used
  `RunLifecycle` (`_BACKLINK_CHAIN_LIFECYCLE`, `extra_columns=("domain",)`)
  for the mechanical create/exists/update_status/resolve_id/delete part
  of `backlink_chains`, same as `_BACKLINK_LIFECYCLE`/`_CRAWL_LIFECYCLE`
  -- it fit cleanly with no gaps, so no manual reimplementation was
  needed there. `link_chain_tier`/`get_backlink_chain_summary` (the
  JOIN-into-per-tier-`BacklinkCheckSummary` logic) are hand-written on
  top, since that shape is chain-specific and not something
  `RunLifecycle` generalizes. New `Database` methods:
  `create_backlink_chain`, `link_chain_tier`,
  `update_backlink_chain_status`, `get_backlink_chain_summary` (orders
  tiers by `tier_index`, reuses `get_backlink_check_summary` per tier --
  no duplicated aggregate-counting SQL), `list_backlink_chains`,
  `resolve_backlink_chain_id`.
- `cli.py`: `bl-chain <tier1> <tier2> [...] -d/--domain ...` command,
  mirroring `bl-check`'s flags plus `--require-confirmed-parent`.
  Rejects <2 tier files and `--headed` without `--browser` up front.
  Loads every tier's candidates before any request goes out (fail fast
  on a bad input file). Tier 1 checked against `frozenset({domain})`;
  tier N>1's default target set is `{normalize_domain(u) for u in ...}`
  over either *all* of tier N-1's `CandidateURL.raw_url`s (default) or
  only the ones tier N-1 confirmed (`--require-confirmed-parent`) --
  `normalize_domain` doubles as "extract hostname from a URL" here since
  it already strips scheme/path, so no separate `urlparse` call was
  needed. Per-row `|target` override (`_build_per_url_targets`, reused
  unmodified) still wins per-URL. Each tier runs through
  `run_backlink_checks`/`run_backlink_checks_browser` unmodified via a
  new `_run_backlink_chain_tier` helper (parallel to `_run_backlink_check`
  but parametrized on `default_targets` and returning the in-memory
  `list[BacklinkResult]` so the next tier's derivation doesn't need a
  second DB round-trip), persisted as an ordinary `backlink_checks`/
  `backlink_results` run and linked via `link_chain_tier`. One progress
  bar per tier, then a combined `_print_backlink_chain_summary_table`,
  ending with `chain_id`.
- Tests: `tests/test_backlink.py` gained a `TestBacklinkChainPersistence`
  class (create/fetch, status update, tier-linking-out-of-order builds
  correctly-sorted `tiers`, list, prefix resolution, and a cascade test
  proving chain deletion does *not* delete the underlying
  `backlink_checks`/`backlink_chain_tiers` cleanup only). New
  `tests/test_backlink_chain.py`: 2-tier chain tested in both
  `--require-confirmed-parent` states (proving the derived default target
  set actually differs -- the "all inputs" run confirms a tier-2 row that
  the "confirmed-only" run correctly reports `not_found` for, since the
  unconfirmed tier-1 host drops out of the derived set) plus a per-row
  `|target` override winning over the derived default either way; a
  3-tier chain proving tier 3 derives from tier 2's own inputs.
  `tests/test_cli.py` gained a minimal `TestBlChainCommand` smoke case.
  All via respx (no real sockets), same convention `test_cli.py`'s
  `TestBacklinkCheckCommands` already uses.

**Full regression, verified fresh against the code (not assumed):**
`pytest` — 369 passed, 0 failed (354 pre-existing + 15 new); `ruff check .`
— all checks passed; `mypy redirecthunter` — success, no issues in 28
source files.

**Ticket 04 (`bl-chain-stats`/`bl-chain-show`/`bl-chain-export`) and 05
(docs sync) were NOT touched this session and their file-level
`Status: done` checkboxes are, per the same hand-off's warning, NOT to be
trusted without re-verification against the code** -- the pattern that
prompted this whole session (ticket-file checkboxes marked done with no
corresponding code) has now been observed twice for this one spec, so
treat every remaining ticket in `.scratch/bl-chain/issues/` as unverified
until grepped/read directly, not just this pair. Also still open from
the spec but out of ticket 03's scope: `docs/CLI_REFERENCE.md` (new
command family + `bl-check`'s own reference gaining the `|target` note),
`docs/DATABASE_SCHEMA.md` (new tables), `CHANGELOG.md`, `AGENT.md`'s
short-flag-exception note.

**Process note for cross-session hand-offs specifically:** don't trust a
hand-off's or a ticket file's "done"/"already implemented" claim without
independently verifying against the actual code (`grep`, `view`, or
equivalent) before building on top of it or skipping work because of it.
This applies symmetrically -- the prior session's claim that tickets
01/02 *were* done checked out under direct verification here, and their
claim that 03/04/05 were *not* done (despite checkboxes saying otherwise)
also checked out. Verify both directions, don't assume the hand-off is
either uniformly right or uniformly cautious-to-a-fault.

**Fixed (2026-08-23 second follow-up session) — `mypy redirecthunter`
module-identity collision, plus the resulting real type error, plus a
stray `ruff` import-sort error.** Root cause of the mypy failure
(`Source file found twice under different module names: "export.csv_writer"
and "redirecthunter.export.csv_writer"`), confirmed by direct
experimentation (not assumed): **`redirecthunter/__init__.py` itself was
missing** — every subpackage (`export/`, `plugins/`) had one, but the
top-level `redirecthunter` package directory never did, so mypy treated
it as an implicit namespace package. Passing `mypy redirecthunter` from
the repo root (the invocation `AGENT.md` specifies) then let mypy
discover the same files under two different module-name roots at once.
Verified by reproducing the error, then adding an empty (well,
docstring-only) `redirecthunter/__init__.py` in a scratch copy first —
that alone made the error disappear and let mypy actually type-check all
28 source files, confirming it as the true root cause rather than a
`sys.path`/`MYPYPATH`/`--explicit-package-bases` issue (none of those
were involved; no stray `PYTHONPATH`, no duplicate `.pth` entries beyond
the normal editable install). `strict = true` was left untouched.
Note: running `mypy .` from *inside* `redirecthunter/` (unqualified,
without the fix) produces a different, larger set of "errors" — that's
an artifact of imports like `from redirecthunter.database import X`
resolving against the separately-installed editable package instead of
the files being checked, not a canonical result; don't chase those if
they resurface with an unusual invocation. `AGENT.md`'s specified
`mypy redirecthunter` from repo root is the canonical command.
With the collision fixed, mypy surfaced exactly one genuine type error:
`loader.py`'s CSV per-row `metadata` dict (built via a dict-comprehension
over `zip(header, row)`, both `list[str]`) was inferred as
`dict[str, str]`, but the same dict later gets `metadata["target"] =
targets` assigned where `targets: tuple[str, ...]` (from
`_split_target_list`, part of the `multi-target-per-row` work above) —
incompatible with the inferred value type even though the field it
eventually lands in (`CandidateURL.row_metadata: dict[str, Any]`) is
fine with it. Fixed by annotating that local as
`dict[str, str | tuple[str, ...]]` rather than widening to `Any` or
touching the model. (The analogous JSON-loader `metadata` dict never hit
this because `orjson.loads()` output is `Any`-typed, so mypy never
narrowed it to `dict[str, str]` in the first place — same shape of logic,
different strictness by construction.) Separately, `ruff check .` had
one pre-existing, unrelated `I001` (import-block un-sorted) in
`tests/test_cli.py` from a single long `from redirecthunter.models
import ...` line; fixed with `ruff check . --fix` (diff reviewed:
reformat-only, split into a multi-line import, no behavior change).
Final regression, verified fresh against the code (not assumed from any
prior note): `pytest` — 354 passed, 0 failed; `ruff check .` — all
checks passed; `mypy redirecthunter` — success, no issues in 28 source
files.

**Fixed (2026-08-23 follow-up session) — `matched_target` was never
persisted to SQLite.** A third, distinct bug in the same family as the
two below: `matched_target` (added to `BacklinkResult` by `bl-chain`,
then relied on again by `multi-target-per-row`'s ticket 02 to record
*which* member of a multi-domain target `frozenset` matched) was never
added to the `backlink_results` table at all. `save_backlink_result()`
had no column to write it into and `_row_to_backlink_result()` had none
to read it from, so the value computed by `backlink.py` was silently
dropped on every save — not shifted, like the `bl-export` bug below,
just gone. Fixed with the standard additive migration
(`PRAGMA table_info` + `ALTER TABLE ADD COLUMN`, same pattern as
`robots_meta`/`robots_header`): `database.py`'s `_SCHEMA_SQL`,
`save_backlink_result()`, `_row_to_backlink_result()`, plus `cli.py`'s
`bl-show` (new "Matched target" column) and the dashboard's backlink
drawer/CSV export. See `CHANGELOG.md`'s `[Unreleased]` → `Fixed` and
`docs/DATABASE_SCHEMA.md` for the column reference.
**Why `.scratch/multi-target-per-row/issues/02-*.md` said "no schema
change needed" and was wrong to:** that ticket's claim was true for
`backlink.py`'s matching core (unchanged, already returned
`matched_target` per-result) but didn't hold for `database.py`'s
persistence layer — a different layer entirely. The ticket conflated
"the value already exists on the model" with "the value is already
storable," which is why the gap went unnoticed through both the
original `bl-chain` work and the `multi-target-per-row` work that
depended on it. If a future ticket says "no schema change needed"
because a field "already exists," check that claim against
`database.py`'s actual `_SCHEMA_SQL`/save/row-mapping functions, not
just against the Pydantic model — a field can be real on the model and
still unpersisted.
**Process note for cross-session hand-offs specifically:** the prior
session's hand-off brief for this same task claimed the
`database.py`/`cli.py`/tests portion was "already fixed in a previous
session." That claim was false — none of that code existed in the
repo when this session started; it had to be built from scratch this
session (and was, per the entry above). Don't take an "already done"
claim in a hand-off brief (or in this file) at face value — grep/read
the actual code before assuming prior work landed, especially for
hand-offs that span sessions rather than being self-reported by the
session that just did the work.

`multi-target-per-row` (spec + 3 tickets at
`.scratch/multi-target-per-row/`) landed: `bl-check`/`bl-chain`'s
existing `|target` per-row override (from `bl-chain`, below) now accepts
a `;`-separated list -- `source_url|target1;target2` -- resolving to a
multi-member `frozenset[str]` instead of always one domain. Changes:
`loader.py`'s `_split_target_override` returns `tuple[str, tuple[str, ...]]`
(was `tuple[str, str | None]`); new `_split_target_list` helper shared by
TXT/CSV/JSON; `cli.py`'s `_build_per_url_targets` builds the frozenset
from a tuple (falls back to accepting a bare string for callers that
build `row_metadata` by hand). No changes needed in `backlink.py` --
its matching core already took an arbitrary-size `frozenset[str]` per
URL since `bl-chain`. Docs (`docs/CLI_REFERENCE.md`'s `bl-check`
section -- which had no `|target` documentation at all before this,
so the base single-target syntax got documented too, not just the
multi-target extension), `examples/backlink.txt` (new demo row),
`examples/README.md`, and `CHANGELOG.md` updated. Tests added in
`tests/test_loader.py` (`TestTargetOverrideTxt`/`TestCsvTargetOverride`/
`TestJsonTargetOverride`) and `tests/test_cli.py`
(`TestBuildPerUrlTargets`, including a regression check that a
single-target override produces the identical one-element frozenset it
did before this change). Spec/tickets remain at
`.scratch/multi-target-per-row/` as historical design record, not open
work.

**Fixed (2026-08-23 session):** the 9 pre-existing test failures
previously logged here as "found, not fixed" are resolved. Two distinct
bugs, not one:
- `tests/test_backlink.py`'s `TestCheckOne`, `TestCheckOneCapturesRelAndTarget`,
  and `TestPlaywrightNotInstalled` were calling `check_one()` /
  `check_one_browser()` / `run_backlink_checks_browser()` with a plain
  `str` (e.g. `"medilana.id"`) instead of `frozenset({"medilana.id"})`,
  left over from the `bl-chain` refactor that changed `check_one`'s
  signature to `target_domains: frozenset[str]`. Because
  `hostname_matches_any`/`build_text_mention_pattern_any` iterate over
  whatever they're given, a bare string got iterated character by
  character, corrupting match-type classification (observed: `anchor`
  misreported as `indirect_query`). All bare-string call sites in both
  test classes were updated to `frozenset({"medilana.id"})` — including
  a few that happened to still pass despite the wrong type (their
  assertions didn't depend on exact `match_type`), fixed anyway so no
  call site is left silently exercising the wrong call shape.
- `tests/test_cli.py::test_bl_stats_and_show_and_export_full_workflow`
  turned out to be a **separate, real production bug**, not the same
  root cause: `cli.py`'s `_backlink_result_to_row()` (the `bl-export`
  CSV/JSON row builder) was missing `matched_target`, one of the 16
  columns in `BACKLINK_RESULT_COLUMNS`. CSV output silently shifted
  every column after `target` left by one; `-f json`'s
  `zip(..., strict=True)` raised outright, which is what actually failed
  the test (its CSV-export step earlier in the same test didn't catch
  it — `csv.writer` doesn't enforce row/header length equality). Fixed
  by adding `result.matched_target or ""` at the right position in the
  row list; see `CHANGELOG.md`'s `[Unreleased]` → `Fixed` for the
  public-behavior writeup (this one *does* change `bl-export` output,
  unlike the test-only fix above).
  **Scope note, added in the 2026-08-23 follow-up session above:** this
  entry's fix and "resolved" status are accurate for the specific
  `_backlink_result_to_row()` export bug — they are *not* a claim that
  every `matched_target`-related bug was resolved. A separate,
  unrelated persistence bug (the field missing from the
  `backlink_results` schema entirely) was still present after this fix
  landed and wasn't caught until the follow-up session above found it.
  Read entries in this file as scoped to what they explicitly describe,
  not as a signal that the surrounding feature/field is entirely done.

`run-lifecycle-deepening` — architecture-review-driven refactor
(2026-08-20 session, no `.scratch/` spec/tickets; agreed via the
`/improve-codebase-architecture` → `/implement` flow, not `/to-spec`).
`redirecthunter/run.py`'s `RunLifecycle` now backs `crawl`'s and
`backlink_check`'s lifecycle methods on `Database`; `RunStatus` replaces
the three formerly-identical `ScanStatus`/`CrawlStatus`/
`BacklinkCheckStatus` enums. Landed and tested (327 tests passing,
`ruff`/`mypy --strict` clean). Two pieces of the original design tree are
**not** done and are open follow-ups, not forgotten:
- `scan` is not migrated onto `RunLifecycle` yet — its
  `get_completed_source_urls`/`export_scan_to_sqlite` extras need a home
  (extend the interface, or stay bolt-ons) before that migration is safe.
- `ResultStream` (the save/iter half of the original design) was
  deliberately **not** built — see the "Why `ResultStream` was scoped
  out" entry below. `crawl`'s/`backlink_check`'s save/iter methods are
  unchanged.

See `CONTEXT.md`'s "Run" entry for the settled domain vocabulary.

`backlink-check-cli` (spec + 5 tickets at `.scratch/backlink-check-cli/`)
landed: `bl-check`/`bl-stats`/`bl-show`/`bl-export` are implemented and
shipped in `[Unreleased]`. Spec and tickets remain at
`.scratch/backlink-check-cli/` as historical design record, not open
work — same treatment `target-replace-cli` got below.

`target-replace-cli` landed and shipped as `1.1.0` — its spec and tickets
remain at `.scratch/target-replace-cli/` as historical design record, not
open work.

## Decisions worth remembering (not obvious from the code alone)

- **Why `BACKLINK_RESULT_COLUMNS` moved to `redirecthunter/backlink.py`
  (2026-08-21, follow-up to the `run.py` session) but the row-*value*
  mapping did not:** `backlink_checker.py`'s `write_csv()` and the CLI's
  `bl-export` had hand-written the identical 13-column header list
  independently — a genuine, provable duplication (unlike
  `crawl-export`/`backlink-export`, which are deliberately different
  shapes). Digging into the row-*value* mapping, though, found the two
  already disagree on how they render `match_found`/`blocked`/
  `requires_login`: `bl-export` writes `"yes"`/`"no"`, `write_csv` writes
  raw `True`/`False` (via `csv.writer`). Ticket 01's spec required
  `backlink_checker.py`'s output stay completely unchanged, so unifying
  the row-value mapping too would have been a silent, undocumented CSV
  output change riding on a "just dedup" commit. Only the column
  *names* were extracted; each caller keeps its own row-value mapping.
  If the boolean-rendering divergence is worth fixing later, that's its
  own scope decision with its own changelog entry — not something to
  bundle into a column-name dedup.

- **Why `ResultStream` was scoped out of the `run.py` deepening (only
  `RunLifecycle` was built):** the original design-tree sketch assumed
  `scan`/`crawl`/`backlink_check` results shared one generic save/iter
  shape. Digging in during implementation found real divergence, not
  just repeated code: `CrawlPageResult`/`CrawlLinkResult` already carry
  their own `page_id`/`link_id`/`checked_at` (set upstream by
  `crawler.py`), while `BacklinkResult` carries none of those —
  `save_backlink_result` generates the id and timestamp itself on the
  way into the database. Forcing both through one generic `save()` would
  need an id/timestamp-generation policy toggle per kind, which is
  exactly the shallow-interface problem `RunLifecycle` exists to remove,
  just relocated one level down. Left as a flagged follow-up rather than
  forced through.

- **Why `bl-check`/`bl-stats`/`bl-show`/`bl-export` (not `backlink-check`/
  etc.) and why several of their flags get single-letter aliases
  (`-d/--domain`, `-c/--concurrency`, `-t/--timeout`, `-u/--agent`,
  `-l/--label`, `-f/--format`, `-o/--output`) despite `AGENT.md`'s
  long-flags-only convention:** requested for typing ergonomics on a
  command family expected to run often — the second documented exception
  to that convention, after `redact-target`/`expand-target`'s alias
  exception (which existed for different reasons: honoring an old
  script's muscle memory, not fresh ergonomics). `--exact` and `--strict`
  replace `--no-subdomains`/`--no-indirect` outright (inverted phrasing,
  not just shortened). `redirecthunter`, `crawl`, and `scan` themselves
  were explicitly *not* shortened or renamed — they're production-stable
  (evidenced by `confirmed_backlinks.txt`, the "Production/Stable"
  classifier, and the full existing test/docs surface) and a rename
  would break every existing invocation for no real ergonomic gain over
  the alias below. `bl-export`'s output path is `-o/--output` (an
  option), not a positional argument like `crawl-export OUTPUT` — the
  ticket asked for `-o/--output` explicitly, so it deliberately doesn't
  mirror `crawl-export`'s positional-argument shape here.

- **Why `bl-check` reuses `engine.RateLimiter` even though it has no
  `--rate-limit` flag of its own:** `run_backlink_checks()` in
  `redirecthunter/backlink.py` uses the same bounded-queue-plus-sentinel
  worker pool shape `Engine.run()` uses for `scan` (a fixed candidate
  list, unlike `crawl`'s dynamically-discovered frontier — see the
  `crawl` module note below for why that distinction matters), and paces
  every request through a `RateLimiter(None)` instance for consistency
  with how `scan`/`crawl` already pace requests, even though it's
  currently always unbounded. If `bl-check` ever needs its own
  `--rate-limit` flag, thread it through `BacklinkCheckConfig` into that
  same `RateLimiter(...)` call — don't add a second pacing mechanism.
  `MAX_BODY_BYTES` is *not* enforced on `bl-check` requests the way it is
  in `Engine._send_request` — `check_one()` (shared with the standalone
  root scripts, see ticket 01) fetches via a plain `client.get()`, and
  changing that fetch shape was explicitly out of scope for the
  extraction ticket, which required `backlink_checker.py`'s behavior stay
  byte-for-byte unchanged. If large-body pages become a real problem for
  `bl-check` specifically, that's a new, scoped change to `check_one()`
  weighed against its shared-behavior guarantee, not a silent addition.
- **Why `BacklinkResult` has no `backlink_id`/`result_id`/`checked_at`
  fields:** it's shared with the standalone `backlink_checker.py`/
  `backlink_checker_js.py` scripts (ticket 01), which have no database
  concept at all. `Database.save_backlink_result()` generates those three
  values on the way into `backlink_results`, the same way e.g.
  `save_crawl_page()` generates a `page_id` for a model that doesn't
  carry one itself — don't add DB-specific fields to `BacklinkResult`
  itself, keep that boundary at the `Database` method.
- **Why `rh` was added as a second `[project.scripts]` entry point instead
  of renaming `redirecthunter`:** same ergonomics ask as above, but for
  the binary name itself, without the breaking cost of a rename — `rh`
  and `redirecthunter` both resolve to `redirecthunter.cli:app`, so every
  existing invocation, doc, and test keeps working unchanged. Docs keep
  using the full `redirecthunter` name for clarity; `rh` is purely a
  faster-to-type alias, not a second identity to keep in sync anywhere
  beyond `pyproject.toml`'s `[project.scripts]` table.

- **Why `crawl-show`'s `--type pages`/`--type links` branches now wrap
  `db.iter_crawl_pages()`/`db.iter_crawl_links()` in `contextlib.aclosing(...)`
  instead of a bare `async for`:** both are async generators with a
  `finally: await cursor.close()`. Breaking out of a bare `async for` on
  `--limit` leaves the generator suspended rather than exhausted; Python
  only throws `GeneratorExit` into it later, during GC — by which point
  `crawl_show`'s own `finally: await db.close()` has already closed the
  connection, so the generator's cleanup throws
  `sqlite3.ProgrammingError: Cannot operate on a closed database` from an
  orphaned task nobody awaits. `show` (the `scan`-result equivalent) was
  already correct via `contextlib.aclosing`; `crawl-show` just hadn't been
  brought in line with it. If a new command adds an early-`break` loop
  over any `iter_*` async generator in `database.py`, use
  `contextlib.aclosing(...)` from the start rather than rediscovering this.


- **Why `rel`/`target` (the anchor's `target` HTML attribute, not to be
  confused with `CrawlLinkResult.target_url`) were added directly to
  `BacklinkResult`/`CrawlLinkResult` instead of a new signals table:**
  both are per-occurrence facts about one specific `<a>` tag, same shape
  as the `rel` field `BacklinkResult` already had — not a new detection
  signal or `match_type`, just previously-dropped data from an anchor
  Claude was already visiting. In crawl mode the field is named
  `target_attr` (not `target`) specifically to avoid colliding with
  `CrawlLinkResult.target_url` in code/DB column lists — `backlink_checker.py`
  has no such collision (its field is `BacklinkResult.target`). Threaded
  through `_FrontierItem`, the link-status cache's per-occurrence
  `model_copy(update=...)`, and the worker's catch-all error path the same
  way `raw_href`/`anchor_text` already were — if a future per-anchor
  attribute is needed (e.g. `rev`), follow that same path, not a new table.

- **SUPERSEDED, 2026-08-21 — `backlink_checker.py` / `backlink_checker_js.py` no longer exist.**
  This entry used to explain why they lived at the repo root, then
  `scripts/`, and why they were kept separate from `bl-check` even when
  asked. That reasoning held for two rounds of requests — until a third
  request explicitly proposed absorbing *both* gaps at once: persist
  browser-mode results to the database (removing the "no-database"
  distinction) and add `--browser` as a `bl-check` flag (removing the
  "no JS-rendering in bl-check" distinction). Once both original
  differentiators are gone, keeping two extra files whose only remaining
  distinction is "argparse instead of Typer" stopped being a real
  design decision.
  **What actually happened:** `check_one_browser`/
  `run_backlink_checks_browser` were ported into `redirecthunter/backlink.py`
  (same file as the httpx `check_one`/`run_backlink_checks`, since they
  now share one `bl-check` command rather than two files). `bl-check`
  gained `--browser`/`--headed`/`--nav-timeout`/`--render-wait`, and its
  `-c/--concurrency` default resolves to `4` instead of `8` automatically
  in browser mode (real page loads vs. lightweight requests — see
  `check_one_browser`'s own default in the old `backlink_checker_js.py`
  for where that number came from). Both scripts and `scripts/` itself
  were deleted; `tests/test_backlink_checker.py`'s coverage was ported
  into `tests/test_backlink.py`, plus new tests against a real local
  HTTP server + real headless Chromium (Playwright and Chromium are
  actually available in this environment, so browser-mode tests aren't
  mocked). `requirements-js.txt` was replaced by a `js` extra in
  `pyproject.toml` (`pip install "redirecthunter[js]"`) and also added to
  the `dev` extra, since the test suite now exercises the browser path.
  **What's genuinely new, not just moved:** every `BacklinkResult` now
  also captures `robots_meta` (`<meta name="robots">`) and
  `robots_header` (`X-Robots-Tag` response header) — kept as two
  separate columns rather than merged, because the two can disagree (the
  header takes precedence per Google's own spec) and merging would hide
  that. `target`/`rel` were already captured pre-existing; only the
  robots signal is new. `backlink_results` gained both columns via the
  same additive `ALTER TABLE` migration pattern `body_link`/`rel`/
  `target_attr` used.
  **What's still true from before, unaffected by this:** the case for
  `bl-check` as one Typer command instead of two argparse scripts was
  never about the underlying matching logic being wrong to share — that
  part (`hostname_matches`, `looks_like_login_wall`,
  `looks_like_bot_block_status`) is unchanged and still the single
  source of truth both check modes call into.
- **Why the repo root used to have a full duplicate copy of every module (`cli.py`, `exporter.py`, `models.py`, ... sitting loose next to `redirecthunter/`) and doesn't anymore:** those were stale leftovers from before the project was packaged under `redirecthunter/` — nothing imported them (verified via grep before deleting), and several had already drifted out of sync with the real `redirecthunter/*.py` versions (e.g. the root `cli.py`/`exporter.py` were missing the `--status-code` filter work landed the same session). They were pure clutter with a real risk of someone editing the wrong copy. Deleted outright rather than kept around "just in case" — `git log`/this file are the record if anyone needs the history. If a loose top-level `.py` file ever reappears next to `redirecthunter/`, treat it as the same kind of drift and either fold its content into the real package module or delete it; don't let both copies persist.
- **Why `exporter.py` became the `export/` subpackage:** it was a single flat file doing three unrelated things (row filtering, CSV writing, JSON writing) plus format dispatch, and kept growing every time a new filter (`--has-link-only`, `--status-code`) landed. Split to mirror `plugins/`'s established one-module-per-concern pattern: `filters.py` (`ExportFilter`/`ExportError`, reusable by `show` too), `csv_writer.py`, `json_writer.py`, `service.py` (`Exporter`, the format dispatcher). Only `export/__init__.py`'s re-exports are public — see `docs/ARCHITECTURE.md#the-export-subpackage`. `tests/test_exporter.py` was renamed to `tests/test_export.py` to match; if you add a new export format, add its writer as a new sibling module (`sqlite_writer.py` if that path is ever changed from delegating straight to `database.py`), not by growing `service.py`.
- **Why `redact-target`/`expand-target` get short-flag aliases when every other CLI command doesn't:** the old `url-target-replace.sh` had real operators used to `-d`/`-i`/`-o`/`-f`/`-t`/`-v` (evidenced by `confirmed_backlinks.txt` at repo root — this was production tooling, not an example). Breaking that muscle memory wasn't worth the consistency win. If a future command has no prior CLI convention to honor, default back to long-only flags like the rest of `cli.py`.
- **Why the three known regex limitations in domain-matching aren't being fixed during the Python port:** fixing them was explicitly out of scope for `target-replace-cli` — the ask was maintainability + a new direction, not a better regex. If someone hits one of these in practice and wants it fixed, that's a new, separate spec.
- **Why `redact-target` output is multi-format but `expand-target` is txt-only:** `redact-target`'s output is an archival artifact people pipe into other tools; `expand-target` is a quick preview step before `scan --target` (which already does full `{TARGET}` expansion at scan time) — it doesn't need to carry the same format surface.

- **Why `redirecthunter crawl` is a separate `crawler.py` module (and
  `crawls`/`crawl_pages`/`crawl_links` are separate tables) instead of a
  mode on `Engine`/`scan`:** a crawl's frontier is discovered dynamically
  (each fetched page can enqueue more work) instead of fixed up front,
  which changes the worker-pool termination strategy itself (unbounded
  `asyncio.Queue` + `Queue.join()`, not `Engine`'s bounded queue +
  sentinel values — see `crawler.py`'s module docstring and
  `docs/ARCHITECTURE.md#crawl-mode`) and adds a second job to every fetch
  (on-page SEO extraction, link discovery) that redirect validation has
  no use for. What *is* shared on purpose: `MAX_BODY_BYTES`, `RateLimiter`,
  and the retry/backoff shape are imported straight from `engine.py`
  rather than re-implemented — only the dispatch loop around them
  differs. If a future change needs `scan` and `crawl` to share more
  (e.g. a unified "audit" summary across both), that's worth its own
  design pass rather than quietly merging the two engines.
- **Why a dead internal link sometimes has no `crawl_links` row:** an
  internal link within crawl scope (depth/page budget) is promoted
  straight to a `crawl_pages` fetch — a 404 there is a `crawl_pages` row
  with `status_code >= 400`, not a separate broken-link record.
  `crawl_links` only holds what *isn't* crawled as a page (external
  links, out-of-scope internal links, and repeat occurrences of an
  internal link after its first). `Database.iter_crawl_pages(...,
  broken_links_only=True)` already accounts for both cases via a
  two-branch `EXISTS` query — if you add a new "does this page have a
  problem" filter, check whether it needs the same two-table treatment
  before assuming `crawl_links` alone is the complete picture.
- **What crawl mode deliberately left out of v1** (flag if it turns into
  a real ask): `robots.txt`/`sitemap.xml` awareness, crawl resume (a
  `crawl` restarted from scratch never reuses a prior run's visited set,
  unlike `scan`'s `resume`), sitewide redirect-chain classification reuse
  from `scan` (crawl only needs "did it redirect", not `RedirectType`),
  and a `crawl-export --format sqlite`. Each is a legitimate feature, not
  an oversight — deferred to keep the first version's scope tight; see
  `docs/ARCHITECTURE.md#crawl-mode` for why sqlite export specifically
  wasn't a natural fit for `crawl-export`'s current shape.

## Agent-memory system itself

`AGENT.md` / `CLAUDE.md` / `MEMORY.md` / `CHANGELOG.md` were added to the
repo root to make cross-session pickup cheap. If you restructure how
planning artifacts are stored (currently `.scratch/<slug>/`), update the
"Working conventions" section of `AGENT.md` to match — it's the single
place that convention is documented.
