# 01 — Per-row target override in the loader (`|target` / `target` column/key)

**What to build:** Extend `redirecthunter/loader.py` so a backlink input
row can carry its own explicit target, stored under the reserved
`row_metadata["target"]` key on `CandidateURL`:

- `_iter_txt`: split each already comment/blank-filtered line on the
  *first* unescaped `|`. Left-hand side is `raw_url`; right-hand side, if
  present and non-empty after stripping, becomes `row_metadata["target"]`.
  A line with no `|` behaves exactly as it does today.
- `_iter_csv`: recognize an optional column literally named `target`
  (case-insensitive, same lookup style already used for the URL column).
  When present, pull it into `row_metadata["target"]` instead of leaving
  it as generic metadata; when absent, behavior is unchanged.
- `_iter_json`: recognize an optional `"target"` key on each object entry
  the same way.

This is read only by `bl-check`/`bl-chain`'s call sites in a later
ticket -- `scan`/`crawl` never look at `row_metadata["target"]`, so a
`scan`/`crawl` input file containing a `|` or a `target` column/key is
unaffected.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] `_iter_txt` splits on the first unescaped `|`; URL-only lines unchanged.
- [x] `_iter_csv` recognizes an optional case-insensitive `target` column without disturbing existing column/no-header behavior.
- [x] `_iter_json` recognizes an optional `"target"` key on object entries.
- [x] `tests/test_loader.py` covers: present/absent override, whitespace around either side, a line with more than one `|` (splits only on the first), CSV `target` column present/absent/case variations, JSON `"target"` key present/absent.
- [x] `scan`/`crawl` behavior on files containing `|` or a `target` column/key is unchanged (regression check).
