# Spec: multi-target per-row override (`|target1;target2;...`)

## Problem Statement

`.scratch/bl-chain/spec.md` added a per-row target override to
`bl-check`/`bl-chain` input files: `source_url|target` pins one row to a
specific target domain instead of the run's `-d/--domain` default. That
implementation (`_split_target_override` in `loader.py`,
`_build_per_url_targets` in `cli.py`) always resolves the right-hand side
of `|` into exactly **one** normalized domain --
`frozenset({normalize_domain(raw_target)})` -- even though the matching
core underneath it (`redirecthunter/backlink.py`) was already
generalized by `bl-chain` to accept `target_domains: frozenset[str]` of
*any* size.

That leaves a real operator need unaddressed: a single backlink row that
should count as a match if it links to **any of several** targets --
e.g. a mixed-brand placement where the same article might link to
`medilana.co.id`, a subdomain like `form.medilana.com`, or a
country-variant domain, and any one of them is an acceptable match.
Today that requires either splitting the row into a separate
single-target file (defeating the point of one input list) or
misusing `bl-chain` (which derives multi-target sets automatically from
a prior tier's URLs, not from operator-authored text).

Writing `https://blog.example.com/post|medilana.co.id;form.medilana.com`
today does not error -- it silently normalizes the *entire*
semicolon-joined string as one bogus "domain," which never matches
anything and gives the operator no signal that their input was
misread.

## Solution

Extend the existing `|target` override -- in all three loader formats
(TXT, CSV, JSON) and its CLI consumer -- to accept a `;`-separated list
of targets on the right-hand side, resolving to a multi-member
`frozenset[str]` instead of always a one-element set. A right-hand side
with no `;` keeps behaving exactly as it does today (one-element set) --
this is additive, not a breaking change to the `bl-chain` spec's
existing per-row override.

## User Stories

1. As an operator, I want to write
   `https://blog.example.com/post|medilana.co.id;form.medilana.com;img.medilana.my.id`
   in a `bl-check`/`bl-chain` input file, so that this one row is
   confirmed as a match if it links to *any* of the three listed
   targets, without splitting it into three separate single-target
   files.
2. As an operator, I want a row with a single target after `|`
   (`source_url|target`, no `;`) to keep working exactly as it does
   today, so that every existing backlink file with the current
   override syntax keeps working unmodified.
3. As an operator, I want the CSV `target` column and the JSON
   `"target"` key to accept the same `;`-separated syntax as the TXT
   `|` override, so that the three input formats stay consistent with
   each other (as they already are for the single-target case).
4. As an operator, I want `bl-show`'s `matched_target` column to tell me
   *which* of a row's several listed targets actually matched, so that
   a multi-target row's result is as legible as a single-target row's.
5. As an operator, I want stray whitespace and empty entries in the
   `;`-list (`medilana.co.id; ;form.medilana.com`, trailing `;`) handled
   gracefully -- ignored, not turned into a bogus empty-string "domain"
   -- so that a hand-edited list with a typo degrades safely instead of
   silently corrupting the target set.

## Implementation Decisions

- **Delimiter choice: `;` inside the existing `|`-delimited target
  field.** Not a new top-level file syntax -- `;` only has meaning
  *after* the `|` (or inside the `target` column/key), matching the
  precedent `-H`'s own header value already tolerates literal `;`
  unrelated to this feature (e.g. `Cookie: a=1;b=2`), so the split must
  happen once, deliberately, on the target field only -- never on the
  raw line or on the URL part.
- **`_split_target_override` (loader.py) return type changes** from
  `tuple[str, str | None]` to `tuple[str, tuple[str, ...]]` (empty tuple
  when no override): after `partition("|")`, the right-hand side is
  further split on `;`, each piece `.strip()`-ed, and empty pieces
  dropped. `row_metadata["target"]` is set to this tuple, not a bare
  string, whenever it is non-empty (unset/absent key when the row has no
  override at all, matching today's behavior of only adding the key when
  there's something to add).
- **`_iter_csv` / `_iter_json`**: the `target` column/key's raw string
  value goes through the same `;`-split-and-strip helper before being
  stored, so `row_metadata["target"]` is always a `tuple[str, ...]`
  across all three loaders once this ships -- callers never need to
  branch on "was this a TXT row or a CSV row."
- **`_build_per_url_targets` (cli.py) updated** to read
  `row_metadata["target"]` as a `tuple[str, ...]` and build
  `frozenset(normalize_domain(t) for t in targets)` -- a single-entry
  tuple produces the exact same one-element `frozenset` it does today,
  so this is provably backward compatible for every existing row that
  has no `;` in it.
- **No change to `redirecthunter/backlink.py`.** The matching core
  already accepts an arbitrary-size `frozenset[str]` per URL (added by
  `bl-chain`); this ticket only changes how that set gets *built* from a
  row's override text. `matched_target` (already present on
  `BacklinkResult`) requires no schema change -- it already records
  whichever member of a multi-member set matched.
- **Empty-after-split guard.** If every `;`-separated piece is blank
  (`source_url|;;`), treat the row as having *no* override -- same as
  today's `target_part or None` guard for a bare trailing `|` -- rather
  than yielding an empty `frozenset` (which would mean "matches
  nothing," a confusing silent-fail state indistinguishable from a
  real not-found).
- **`row_metadata["target"]` as tuple is loader-internal.** `scan`/
  `crawl` still never read this key (unchanged from the `bl-chain`
  spec), so the type change is invisible to every other command.

## Testing Decisions

- `tests/test_loader.py`: extend the existing `_iter_txt` `|target`
  cases with multi-target rows (`a|b;c`, `a|b; c ;d`, `a|b;;`,
  `a|;;;` -> no override, `a|b` -> unchanged one-element tuple), plus
  the equivalent cases for `_iter_csv`'s `target` column and
  `_iter_json`'s `"target"` key.
- `tests/test_cli.py` / wherever `_build_per_url_targets` is unit-tested:
  a candidate with a multi-entry `row_metadata["target"]` tuple produces
  the expected multi-member `frozenset`; a single-entry tuple produces
  the same one-element `frozenset` as a bare string did before (backward
  compatibility regression guard).
- `tests/test_backlink_chain.py` / `test_backlink.py`: end-to-end case
  where a row's `|target1;target2` override matches on the second
  listed target and `matched_target` records that one specifically, not
  the first.

## Out of Scope

- Any change to how `bl-chain` derives tier-to-tier default target sets
  (still: all of tier N's input URLs, unrelated to this per-row
  operator-authored syntax).
- A new delimiter for the URL/target split itself -- still `|`,
  unchanged from the `bl-chain` spec.
- Validating that each `;`-separated entry is a syntactically plausible
  domain before normalization -- `normalize_domain` already handles
  malformed input the same way it does for the single-target case
  today; no new validation layer.

## Further Notes

- Docs to update once implementation starts: `docs/CLI_REFERENCE.md`'s
  `bl-check` section (the `|target` row-syntax note gains a line on the
  `;`-list form), `CHANGELOG.md`, `MEMORY.md`.
- `examples/backlink.txt` (or a new example file) should gain one
  real, loader-valid multi-target row per `AGENT.md`'s
  examples-are-verified convention.
