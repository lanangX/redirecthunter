# FAQ

**Is this an exploitation tool?**
No. It validates redirect behavior on URLs you supply — it doesn't
discover open-redirect endpoints for you, doesn't fuzz for
vulnerabilities, and doesn't attempt to defeat any protection (Cloudflare
challenges are classified, never bypassed). Use it against infrastructure
you own or are authorized to test.

**Why does `--method HEAD` (the default) miss meta-refresh and JavaScript
redirects?**
HEAD responses never have a body, and both detection strategies need to
inspect HTML/inline `<script>` content. Use `--method GET` when you need
full redirect-type coverage; HEAD remains the default because it's
faster and sufficient for pure HTTP-status auditing.

**`redirecthunter export ... --has-link-only` (or `show ... --has-link-only`)
always gives me 0 results, even though the pages clearly have `<a href>`
links in their body. Why?**
Same root cause as the meta-refresh/JS question above: `body_link` is
extracted from the response body, and a scan run with the default
`--method HEAD` never fetches one — so `body_link` is `NULL` for every
result regardless of what's actually on the page. This is why a scan can
report hundreds of redirects (detected from headers alone) while its
`--has-link-only` export comes back empty. Fix: re-run (or `resume`) the
scan with `--method GET`, then `--has-link-only` will pick up any
navigable `<a href>` in the terminal response's body, as intended. The
`scan` command also prints a warning at start time whenever it's about
to run with HEAD, precisely so this doesn't surprise you after the fact.

**How do I export or view only certain status codes?**
`--status-code` on both `export` and `show` accepts an exact code
(`--status-code 301`) or a whole response class (`--status-code 3xx`).
It's repeatable and comma-separated, so `--status-code 301,302 --status-code
4xx` matches any 301, any 302, or anything in the 4xx range. It combines
(AND) with the other `--*-only` filters, e.g. `--redirects-only
--status-code 3xx`.

**How is `hop_count` different from "number of requests made"?**
`hop_count` counts redirects followed, not total HTTP requests. A direct
200 has `hop_count = 0` even though one request was made. See
[Understanding the results](../README.md#understanding-the-results) in
the main README.

**Can I point it at a SQLite input file with a different schema?**
Yes — `--input-table` and `--input-column` override the `urls`/`url`
defaults.

**Do I have to type the full scan_id UUID every time?**
No — every command accepts an unambiguous prefix, like a git short hash.
`redirecthunter stats` shows the first 8 characters in its listing for
exactly this reason; `redirecthunter show 3f9a1c2e` works directly. If a
prefix matches more than one scan, the command tells you and asks for more
characters rather than guessing.

**How do I find redirects that go somewhere unexpected (outside my target domain)?**
`redirecthunter find <scan_id>` — auto-detects the domain from the scan's
`--target`, or pass `--domain` explicitly. Add `--output file.txt` to save
a plain list of just the external destination URLs, one per line. Add
`--invert` to flip the question around: which source URLs *correctly*
redirect to your domain? (Useful since many public redirect/ad-click
services don't reliably forward where you tell them to.)

**How do I delete a scan I don't need anymore, so the database doesn't keep growing?**
`redirecthunter delete <scan_id>` removes the scan and everything tied to
it. SQLite doesn't shrink the file automatically after a delete though —
run `redirecthunter vacuum` afterwards (or pass `--vacuum` to `delete`
directly) to actually reclaim the disk space.

**Does it store full response headers for every redirect hop?**
Only for the terminal (final) response, in the `headers` table —
intermediate hops' relevant fields (status, `Location`, `Server`) are
already on the `chain` table. See
[`DATABASE_SCHEMA.md`](./DATABASE_SCHEMA.md#headers).

**What happens if a scan is interrupted (Ctrl+C, crash, network outage)?**
The scan is marked `interrupted` (or `failed`, on an unhandled exception)
in the database, and every result already recorded stays there.
`redirecthunter resume <scan_id>` continues from exactly where it stopped.

**What's the difference between `bl-check` and `bl-chain`?**
`bl-check` verifies one file of URLs against one target domain.
`bl-chain` runs that same check across a *tiered* (pyramid) link
structure — tier 1 vs. your domain, tier 2 vs. tier 1's own hosts, and so
on — in a single command, instead of you manually re-running `bl-check`
per tier and building each tier's target list by hand. See
[`BACKLINK_GUIDE.md`](./BACKLINK_GUIDE.md#chained-tiered-audits-redirecthunter-bl-chain)
for the full walkthrough.

Back to [main README](../README.md).
