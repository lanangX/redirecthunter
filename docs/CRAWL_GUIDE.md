# Crawling a site (`redirecthunter crawl`)

`scan` answers *"where does this specific URL redirect to?"* for a fixed
list you already have. `crawl` answers the different, Ahrefs-Site-Audit-
style question: *"starting from this page (or this list), what does the
whole reachable site look like, and what's broken on it?"*

Two ways to start a crawl:

```bash
# Domain mode: discover pages by following internal links from a seed
redirecthunter crawl https://example.com

# URL-list mode: audit exactly the URLs in a file (same formats as `scan`)
redirecthunter crawl --input-file urls.txt

# URL-list mode, but don't discover further pages from them -- just
# audit those exact URLs and check what they link to
redirecthunter crawl --input-file urls.txt --no-follow-links
```

Both modes share one crawl engine and one set of checks. For every
in-scope page fetched, `crawl` records:

- **On-page SEO issues**: missing or too-short/too-long `<title>`,
  missing or too-long meta description, missing or duplicated `<h1>`,
  duplicate titles/meta descriptions across the whole crawl.
- **Broken links**: every link found — internal or external — is checked
  for a 4xx/5xx status or a transport-level failure. A broken *internal*
  link that's within crawl scope becomes its own crawled page (so you see
  its actual status code, not just "broken"); everything else (external
  links, out-of-scope internal links, repeat occurrences) lands in a
  separate checked-links table. See
  [`DATABASE_SCHEMA.md`](./DATABASE_SCHEMA.md#crawl-mode-tables) for
  exactly which table a given broken link ends up in.

Useful flags: `--max-depth` / `--max-pages` bound how far and how much it
crawls; `--no-check-external-links` skips requesting external links
entirely (counts them, doesn't validate them — faster, and avoids hitting
third-party sites at all); `--allowed-domain` extends "internal" scope to
extra hostnames (e.g. a separate blog subdomain you still want crawled as
part of the same audit); `--include-query-string`/`--no-include-query-string`
controls whether `?sort=price` vs `?sort=name` count as different pages.

```bash
redirecthunter crawl-stats                     # list all crawls
redirecthunter crawl-stats 3f9a1c2e             # one crawl's summary
redirecthunter crawl-show 3f9a1c2e --issues-only
redirecthunter crawl-show 3f9a1c2e --type links --broken-only
redirecthunter crawl-export 3f9a1c2e pages.csv
redirecthunter crawl-export 3f9a1c2e broken_links.csv --type links --broken-only
```

Results share the same SQLite database as `scan` (in separate tables) —
`--database`/`--db` works the same way on every `crawl*` command.

Full flag-by-flag reference for every `crawl*` command:
[`CLI_REFERENCE.md`](./CLI_REFERENCE.md#crawl).

Table/column-level detail on what's stored where:
[`DATABASE_SCHEMA.md`](./DATABASE_SCHEMA.md#crawl-mode-tables).

Back to [main README](../README.md).
