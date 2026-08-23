# CONTEXT.md

Domain vocabulary for RedirectHunter, beyond what `AGENT.md` already
covers. Add a term here the moment it's named during a design
conversation — don't wait for the code to land first.

## Run

One execution of `scan`, `crawl`, or `bl-check`, tracked start-to-finish:
created, given an id, worked, and eventually completed/interrupted/failed.
Every run kind persists to its own lifecycle row (`scans`/`crawls`/
`backlink_checks`) plus one or more result rows.

Settled during the architecture review on 2026-08-20 (see that session for
the full design-tree trace):

- **RunLifecycle** — the seam owning create/status/list/resolve-id/delete
  for one run kind. One per kind (scan, crawl, backlink-check).
- **ResultStream** — the seam owning save/iter for one result table
  belonging to a run. Most kinds wire up exactly one; `crawl` wires up
  two (`crawl_pages` and `crawl_links`) sharing a single `RunLifecycle`,
  since a crawl's pages and its checked links are genuinely two different
  things being recorded, not one.
- Both are code-side seams only — parameterized by table name, PK column,
  and (for `ResultStream`) a row-to-model mapping — not a schema
  migration. Existing `redirecthunter.db` files and column layouts are
  unaffected.
- `RunStatus` (unifying the formerly-identical `ScanStatus`/`CrawlStatus`/
  `BacklinkCheckStatus`) is the one shared enum across every run kind.
- Per-kind config (`ScanConfig`/`CrawlConfig`/`BacklinkCheckConfig`) stays
  separate and is *not* part of this deepening — `CrawlConfig`'s own
  docstring already argues against merging config shapes, and that
  reasoning still holds. Only the mechanical persistence lifecycle is
  being deepened.
